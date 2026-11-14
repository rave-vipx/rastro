"""`rastro health`: stale sources, open drift, quarantined candidates and last success per
current source of a job (DATA-004, DRIFT-005, REPAIR-005, OPS). Reads only; creates nothing.
"""

from __future__ import annotations

from pathlib import Path

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.active_recipes import source_key
from rastro.application.job_dataset import dataset_location
from rastro.application.job_loader import load_job, resolve_job_path
from rastro.application.job_maintenance import current_source_urls
from rastro.domain.dataset import SourceStatus
from rastro.domain.health import Attention, JobHealth, OpenDrift, SourceHealth
from rastro.domain.jobs import Job
from rastro.domain.lifecycle import RecipeState


def job_health(path: str | Path, *, allow_absolute_paths: bool = False) -> JobHealth:
    """Health of each current source; ``healthy`` is false when any source needs attention."""
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    location = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    assert job.dataset is not None
    statuses, drift = _dataset_state(job, location)
    quarantined = _quarantined(job, file, allow_absolute_paths)
    urls = current_source_urls(job)
    sources = tuple(
        _source(index, url, statuses.get(url), drift.get(url), quarantined[index - 1])
        for index, url in enumerate(urls, start=1)
    )
    return JobHealth(
        dataset=job.dataset.name,
        healthy=not any(source.attention for source in sources),
        sources=sources,
        orphaned_sources=tuple(url for url in statuses if url not in set(urls)),
    )


def _dataset_state(
    job: Job, location: Path
) -> tuple[dict[str, SourceStatus], dict[str, OpenDrift]]:
    assert job.dataset is not None
    name = job.dataset.name
    if not location.is_file():
        return {}, {}
    store = SQLiteDatasetStore(location, limits=job.limits)
    if name not in store.dataset_names():
        return {}, {}
    drift = SQLiteDriftMonitor(location, limits=job.limits).open_drift(name)
    return (
        {status.source_url: status for status in store.source_status(name)},
        {item.source_url: item for item in drift},
    )


def _quarantined(job: Job, file: Path, allow_absolute_paths: bool) -> list[tuple[int, ...]]:
    """Candidate versions waiting for review per source; none without a recipe memory file."""
    empty: list[tuple[int, ...]] = [() for _ in job.sources]
    if job.recipe_memory is None:
        return empty
    memory = resolve_job_path(
        job.recipe_memory, base_dir=file.parent, allow_absolute_paths=allow_absolute_paths
    )
    if not memory.is_file():
        return empty
    repository = SQLiteRecipeRepository(memory, read_only=True)
    return [
        tuple(
            version.version
            for version in repository.history(source_key(job, item)).versions
            if version.state == RecipeState.CANDIDATE
        )
        for item in job.sources
    ]


def _source(
    index: int,
    url: str,
    status: SourceStatus | None,
    drift: OpenDrift | None,
    quarantined: tuple[int, ...],
) -> SourceHealth:
    attention: list[Attention] = []
    if status is None:
        attention.append("never_loaded")
    elif status.stale:
        attention.append("stale")
    if drift is not None:
        attention.append("open_drift")
    if quarantined:
        attention.append("quarantined_candidate")
    return SourceHealth(
        source_index=index,
        source_url=url,
        last_success=status.last_success if status else None,
        last_attempt=status.last_attempt if status else None,
        stale=status.stale if status else None,
        reason=status.reason if status else None,
        detail=status.detail if status else None,
        open_drift=drift,
        quarantined_versions=quarantined,
        attention=tuple(attention),
    )
