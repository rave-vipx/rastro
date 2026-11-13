"""Run a job and load its approved fields into the dataset (DATA-001/003, SYNC-008)."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.dataset_load import load_results
from rastro.application.drift import collect_snapshots
from rastro.application.job_loader import load_job, resolve_job_path
from rastro.application.jobs import run_job
from rastro.application.success_samples import collect_successes
from rastro.domain.job_load import JobLoadResult
from rastro.domain.models import ConfigurationError

if TYPE_CHECKING:
    from rastro.domain.drift import StructuralSnapshot


def load_job_file(
    path: str | Path,
    *,
    drift: bool = False,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_command: bool = False,
    allow_env: Sequence[str] = (),
) -> JobLoadResult:
    """Run a job file and load its approved fields into the job's ``[dataset]``.

    Raises ``ConfigurationError`` for invalid jobs or paths. With ``drift=True`` the
    structural drift of each source is measured and reported. Each loaded source
    reports whether its recipe came from the job file or from ``recipe_memory``.
    ``allow_*`` are the operator's model opt-ins, as for ``run_job``.
    """
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("job load requires a [dataset] block in the job file")
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
    raw_path = (
        Path(spec.path) if Path(spec.path).is_absolute() else file.parent.resolve() / spec.path
    )
    store = SQLiteDatasetStore(
        raw_path,
        limits=job.limits,
        near_match_fields=spec.match_fields,
        near_match_tolerance=timedelta(minutes=spec.match_tolerance_minutes or 30),
    )
    monitor = SQLiteDriftMonitor(raw_path, limits=job.limits) if drift else None
    capture: AbstractContextManager[dict[str, StructuralSnapshot]]
    if drift:
        from rastro.adapters.drift_probe import ScraplingDriftProbe

        capture = collect_snapshots(ScraplingDriftProbe())
    else:
        capture = nullcontext({})
    with capture as snapshots, collect_successes() as successes:
        report = run_job(
            job,
            base_dir=file.parent,
            allow_absolute_paths=allow_absolute_paths,
            allow_remote=allow_remote,
            allow_command=allow_command,
            allow_env=allow_env,
        )
    loaded = load_results(
        spec,
        [f.name for f in job.fields],
        report,
        store,
        rules=job.consistency,
        monitor=monitor,
        snapshots=snapshots,
    )
    from rastro.application.repair_storage import retain_successes

    held_urls = {
        s.source_url
        for s in loaded.sources
        if s.consistency is not None and s.consistency.verdict == "inconsistent"
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
                        "issues": issues.get(source.source_url, ()),
                        "recipe_origin": origins[index].recipe_origin,
                        "recipe_version": origins[index].recipe_version,
                    }
                )
                for index, source in enumerate(loaded.sources, start=1)
            )
        }
    )
