"""Run a job and load its approved fields into the dataset (DATA-001/003, SYNC-008)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.auth_headers import check_job_auth
from rastro.application.checkpointed_load import run_checkpointed_load
from rastro.application.conditional import ConditionalRequests
from rastro.application.conditional_inputs import extraction_inputs
from rastro.application.dataset_open import dataset_location, open_dataset
from rastro.application.drift import collect_snapshots
from rastro.application.egress_trust import check_job_egress
from rastro.application.job_ladder import JobLadder
from rastro.application.job_loader import (
    build_job_model,
    load_job,
    recipe_memory_path,
)
from rastro.application.jobs import run_job
from rastro.application.model_budget import model_cost_override
from rastro.application.operator_trust import operator_trust
from rastro.application.run_lock import dataset_lock
from rastro.application.run_report import report_for_load
from rastro.application.success_samples import collect_successes
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import UrlJobSource
from rastro.domain.models import ConfigurationError, Outcome
from rastro.ports.dataset import DatasetValidators

# The dataset helpers moved to ``dataset_open``; this module keeps exporting them.
__all__ = ["dataset_location", "load_job_file", "open_dataset"]

if TYPE_CHECKING:
    from rastro.domain.drift import StructuralSnapshot
    from rastro.domain.job_sources import JobSource
    from rastro.domain.jobs import Job
    from rastro.ports.acquisition import AcquisitionPort


def _positions(job: Job, sources: Sequence[JobSource]) -> tuple[int, ...]:
    """Where each of `sources` sits in the job file, so checkpoints name them stably."""
    try:
        return tuple(job.sources.index(source) for source in sources)
    except ValueError:
        raise ConfigurationError("Sources to load must come from the job file") from None


def load_job_file(
    path: str | Path,
    *,
    drift: bool = False,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    allow_private_network: bool = False,
    allow_ports: Iterable[int] = (),
    ignore_robots: Iterable[str] = (),
    max_model_cost: float | None = None,
    model_cost_per_call: float | None = None,
    sources: Sequence[JobSource] | None = None,
    should_stop: Callable[[], bool] | None = None,
    _lock_held: bool = False,
) -> JobLoadResult:
    """Run a job file and load its approved fields into the job's ``[dataset]``.

    Raises ``ConfigurationError`` for invalid jobs or paths. With ``drift=True`` the
    structural drift of each source is measured and reported. Each loaded source
    reports whether its recipe came from the job file or from ``recipe_memory``.
    ``allow_*`` and ``ignore_robots`` are the operator's opt-ins, as for ``run_job``.
    ``max_model_cost`` and ``model_cost_per_call`` limit the job's model for the whole load,
    not per source (COST-002).
    """
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    positions = tuple(range(len(job.sources)))
    if sources is not None:
        positions = _positions(job, sources)
        job = job.model_copy(update={"sources": tuple(sources)})
    allow_env = tuple(allow_env)
    check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env)
    check_job_auth(job, allow_env=allow_env, allow_private_network=allow_private_network)
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("job load requires a [dataset] block in the job file")
    raw_path = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    timeout = job.acquisition.operation_timeout_seconds if job.acquisition is not None else 60.0
    drift_state = SQLiteDriftMonitor(raw_path, limits=job.limits)
    monitor = drift_state if drift else None
    capture: AbstractContextManager[dict[str, StructuralSnapshot]]
    if drift:
        from rastro.adapters.drift_probe import ScraplingDriftProbe

        capture = collect_snapshots(ScraplingDriftProbe())
    else:
        capture = nullcontext({})
    with (
        dataset_lock(
            raw_path, timeout_seconds=timeout, source_count=len(job.sources), held=_lock_held
        ),
        open_dataset(job, raw_path, allow_remote=allow_remote, allow_env=allow_env) as store,
    ):
        conditional = (
            ConditionalRequests(
                store,
                spec.name,
                extraction_inputs(
                    job, base_dir=file.parent, allow_absolute_paths=allow_absolute_paths
                ),
            )
            if isinstance(store, DatasetValidators) and job.acquisition is not None
            else None
        )
        with (
            operator_trust(
                allow_remote=allow_remote, allow_stealth=allow_stealth, allow_env=allow_env
            ),
            model_cost_override(max_model_cost, model_cost_per_call),
        ):  # the render-mode consent check runs here, before any source is run
            acquisition = _load_acquirer(job, conditional)
            model = build_job_model(job)  # one budget and one notice for every source
        ladder = JobLadder(
            job,
            acquisition,
            build=lambda rung_job: _web_acquirer(rung_job, conditional),
            scope=lambda: operator_trust(
                allow_remote=allow_remote, allow_stealth=allow_stealth, allow_env=allow_env
            ),
            rotate=allow_egress,
            memory=_memory(job, file, allow_absolute_paths),
        )
        with capture as snapshots, collect_successes() as successes:
            report, loaded, ran = run_checkpointed_load(
                raw_path,
                job,
                store,
                extract_fn=lambda sub_job: ladder.run(
                    sub_job,
                    lambda rung_job, port: run_job(
                        rung_job,
                        base_dir=file.parent,
                        allow_absolute_paths=allow_absolute_paths,
                        allow_remote=allow_remote,
                        allow_egress=allow_egress,
                        allow_stealth=allow_stealth,
                        allow_env=allow_env,
                        allow_private_network=allow_private_network,
                        allow_ports=allow_ports,
                        ignore_robots=ignore_robots,
                        acquisition=port,
                        model=model,
                        max_model_cost=max_model_cost,
                        model_cost_per_call=model_cost_per_call,
                    ),
                ),
                positions=positions,
                monitor=monitor,
                snapshots=snapshots,
                drift_state=drift_state,
                should_stop=should_stop,
            )
        if conditional is not None:
            loaded = _commit_validators(conditional, ran, report, loaded)
    from rastro.application.repair_storage import retain_successes

    held_urls = {
        s.source_url
        for s in loaded.sources
        if s.inconsistent or any(i.code == "storage_error" for i in s.issues)
    }
    for result in report.results:
        if result.source_url in held_urls:
            successes.pop(result.job_id, None)
    issues = retain_successes(raw_path, spec.name, job.build_schema(), report, successes)
    origins = {s.source_index: s for s in report.recipes}
    loaded = loaded.model_copy(
        update={
            "sources": tuple(
                source.model_copy(
                    update={
                        "issues": (*source.issues, *issues.get(source.source_url, ())),
                        "recipe_origin": origins[index].recipe_origin,
                        "recipe_version": origins[index].recipe_version,
                    }
                )
                for index, source in enumerate(loaded.sources, start=1)
            )
        }
    )
    price = report.report.totals.model_cost_per_call if report.report else None
    return loaded.model_copy(update={"report": report_for_load(file.name, report, loaded, price)})


def _memory(job: Job, file: Path, allow_absolute_paths: bool) -> SQLiteRecipeRepository | None:
    """The job's recipe memory, where the ladder keeps each recipe's start level."""
    path = recipe_memory_path(job, base_dir=file.parent, allow_absolute_paths=allow_absolute_paths)
    return SQLiteRecipeRepository(path) if path is not None else None


def _load_acquirer(job: Job, conditional: ConditionalRequests | None) -> AcquisitionPort | None:
    """The load's one acquisition port: host pacing, robots and the cookie session span sources.

    Built inside the operator's trust scope; browser modes need it (``allow_stealth``).
    """
    if job.acquisition is None or not any(isinstance(s, UrlJobSource) for s in job.sources):
        return None
    return _web_acquirer(job, conditional)


def _web_acquirer(job: Job, conditional: ConditionalRequests | None) -> AcquisitionPort:
    """HTTP acquisition, or the browser composition when a source renders (SCR-003).

    Also builds each acquisition ladder level above the job's own, from a copy of the job
    at that level.
    """
    try:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer
        from rastro.browser import job_acquisition

        assert job.acquisition is not None
        return job_acquisition(job, None, conditional=conditional) or WebAcquirer(
            job.acquisition,
            transport=CurlTransport(),
            resolver=SystemResolver(),
            conditional=conditional,
        )
    except ModuleNotFoundError as error:
        if error.name not in {"curl_cffi", "protego"}:
            raise
        raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None


def _commit_validators(
    conditional: ConditionalRequests,
    ran: Sequence[JobSource],
    report: JobRunResult[Any],
    loaded: JobLoadResult,
) -> JobLoadResult:
    """Save validators only for the ``ran`` sources that loaded cleanly (a stopped or resumed run
    ran a subset of the job); storage failures are explicit."""
    try:
        for item, result, source in zip(ran, report.results, loaded.sources, strict=True):
            if (
                isinstance(item, UrlJobSource)
                and source.status == Outcome.SUCCESS
                and not source.inconsistent
                and not source.issues
            ):
                conditional.save(item.url, result.completed_at)
    except DatasetStorageError:
        conditional.failed = True
    if conditional.failed:
        return loaded.model_copy(update={"status": "storage_error"})
    return loaded
