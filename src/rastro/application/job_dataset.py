"""Run a job and load its approved fields into the dataset (DATA-001/003, SYNC-008)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.checkpointed_load import run_checkpointed_load
from rastro.application.drift import collect_snapshots
from rastro.application.egress_trust import allowed_env_names, check_job_egress
from rastro.application.job_loader import load_job, resolve_job_path
from rastro.application.jobs import run_job
from rastro.application.operator_trust import current_trust, operator_trust
from rastro.application.run_lock import dataset_lock
from rastro.application.success_samples import collect_successes
from rastro.domain.job_load import JobLoadResult
from rastro.domain.jobs import UrlJobSource
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name
from rastro.ports.dataset import DatasetStore
from rastro.scrape_target import web_acquirer

if TYPE_CHECKING:
    from rastro.domain.drift import StructuralSnapshot
    from rastro.domain.job_sources import JobSource
    from rastro.domain.jobs import Job
    from rastro.ports.acquisition import AcquisitionPort


def dataset_location(file: Path, job: Job, *, allow_absolute_paths: bool) -> Path:
    """The job's dataset path, contained like every job path; symlinks are left for the adapter.

    Raises ``ConfigurationError`` without a ``[dataset]`` block or a dataset folder.
    """
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("This command requires a [dataset] block in the job file")
    resolved = resolve_job_path(
        spec.path, base_dir=file.parent, allow_absolute_paths=allow_absolute_paths
    )
    if resolved == file.resolve():
        raise ConfigurationError("The dataset must be separate from the job file")
    if not resolved.parent.is_dir():
        raise ConfigurationError(
            f"Dataset folder does not exist: {spec.path} (paths are relative to the job file)",
            location="dataset.path",
        )
    # Preserve symlink components for the adapter's refusal, after containment validation.
    return Path(spec.path) if Path(spec.path).is_absolute() else file.parent.resolve() / spec.path


def _positions(job: Job, sources: Sequence[JobSource]) -> tuple[int, ...]:
    """Where each of `sources` sits in the job file, so checkpoints name them stably."""
    try:
        return tuple(job.sources.index(source) for source in sources)
    except ValueError:
        raise ConfigurationError("Sources to load must come from the job file") from None


def load_acquisition(job: Job) -> AcquisitionPort | None:
    """One acquisition port for the whole load: host pacing, robots and sessions span sources.

    Call inside the operator's trust scope; browser modes need it (``allow_stealth``).
    """
    from rastro.browser import job_acquisition

    if job.acquisition is None or not any(isinstance(s, UrlJobSource) for s in job.sources):
        return None
    if (shared := job_acquisition(job, None)) is not None:
        return shared
    try:
        return web_acquirer(job.acquisition)
    except ImportError:
        raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None


def load_job_file(
    path: str | Path,
    *,
    drift: bool = False,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    sources: Sequence[JobSource] | None = None,
    should_stop: Callable[[], bool] | None = None,
    _lock_held: bool = False,
) -> JobLoadResult:
    """Run a job file and load its approved fields into the job's ``[dataset]``.

    Raises ``ConfigurationError`` for invalid jobs or paths. With ``drift=True`` the
    structural drift of each source is measured and reported. Each loaded source
    reports whether its recipe came from the job file or from ``recipe_memory``.
    ``allow_*`` are the operator's opt-ins, as for ``run_job``.
    """
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    positions = tuple(range(len(job.sources)))
    if sources is not None:
        positions = _positions(job, sources)
        job = job.model_copy(update={"sources": tuple(sources)})
    allow_env = tuple(allow_env)
    check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env)
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
    with operator_trust(
        allow_remote=allow_remote, allow_stealth=allow_stealth, allow_env=allow_env
    ):
        acquisition = load_acquisition(job)
    with (
        dataset_lock(
            raw_path, timeout_seconds=timeout, source_count=len(job.sources), held=_lock_held
        ),
        open_dataset(job, raw_path, allow_remote=allow_remote, allow_env=allow_env) as store,
        capture as snapshots,
        collect_successes() as successes,
    ):
        report, loaded = run_checkpointed_load(
            raw_path,
            job,
            store,
            extract_fn=lambda sub_job: run_job(
                sub_job,
                base_dir=file.parent,
                allow_absolute_paths=allow_absolute_paths,
                allow_remote=allow_remote,
                allow_egress=allow_egress,
                allow_stealth=allow_stealth,
                allow_env=allow_env,
                acquisition=acquisition,
            ),
            positions=positions,
            monitor=monitor,
            snapshots=snapshots,
            drift_state=drift_state,
            should_stop=should_stop,
        )
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
    return loaded.model_copy(
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


@contextmanager
def open_dataset(
    job: Job, local_path: Path, *, allow_remote: bool = False, allow_env: Iterable[str] = ()
) -> Iterator[DatasetStore]:
    """Open the configured dataset; operational state always uses local_path."""
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("This command requires a [dataset] block")
    tolerance = timedelta(minutes=spec.match_tolerance_minutes or 30)
    if spec.backend == "sqlite":
        yield SQLiteDatasetStore(
            local_path,
            limits=job.limits,
            near_match_fields=spec.match_fields,
            near_match_tolerance=tolerance,
        )
        return
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    assert spec.dsn_env is not None
    name = check_secret_env_name(spec.dsn_env, allowed_env_names(allow_env))
    dsn = os.environ.get(name)
    if not dsn:
        raise ConfigurationError("PostgreSQL connection environment variable is missing or empty")
    store = PostgresDatasetStore(
        dsn,
        schema=spec.db_schema,
        allow_remote=allow_remote or current_trust().allow_remote,
        limits=job.limits,
        near_match_fields=spec.match_fields,
        near_match_tolerance=tolerance,
    )
    try:
        yield store
    finally:
        store.close()
