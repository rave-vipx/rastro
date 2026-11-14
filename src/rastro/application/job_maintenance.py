"""Operator maintenance of a job dataset: acknowledge drift, forget a source (ADR 0022)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rastro.application.job_dataset import dataset_location
from rastro.application.job_loader import load_job
from rastro.domain.jobs import FileJobSource, Job
from rastro.domain.models import ConfigurationError, Source
from rastro.domain.repair import digest


def job_sources(job: Job) -> tuple[Source, ...]:
    """Each configured source with its regional context, in job order."""
    return tuple(
        (item.region or job.region).to_source(
            item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
        )
        for item in job.sources
    )


def current_source_urls(job: Job) -> tuple[str, ...]:
    """The dataset origins (query-free evidence URLs) of the job's current sources."""
    return tuple(source.evidence_url for source in job_sources(job))


def acknowledge_job_drift(
    path: str | Path, source: int | None = None, *, allow_absolute_paths: bool = False
) -> tuple[str, ...]:
    """Acknowledge open drift of one source (1-based) or of every source when ``None``.

    The next run compares with the latest successful run, so the drifted structure becomes
    the new normal. Returns the source URLs whose open drift was cleared.
    """
    from rastro.adapters.sqlite_dataset_admin import acknowledge_drift

    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    location = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    assert job.dataset is not None
    urls = current_source_urls(job)
    if source is not None:
        if not 1 <= source <= len(urls):
            raise ConfigurationError("Source index is outside the job sources", location="source")
        urls = (urls[source - 1],)
    return tuple(
        url
        for url in dict.fromkeys(urls)
        if acknowledge_drift(location, job.dataset.name, url, limits=job.limits)
    )


def forget_job_source(
    path: str | Path, source_url: str, *, allow_absolute_paths: bool = False
) -> dict[str, Any]:
    """Forget a dataset origin that is no longer one of the job's sources (DATA-004).

    Deletes its source status, drift history and open drift, plus the relocation samples
    of every source no longer in the job (they are stored by digest, not URL). Stored
    records keep their values and provenance. A current source is refused.
    """
    from rastro.adapters.sqlite_dataset_admin import forget_source

    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    location = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    assert job.dataset is not None
    sources = job_sources(job)
    if source_url in {source.evidence_url for source in sources}:
        raise ConfigurationError(
            "This URL is a current job source; remove it from the job file first",
            location="url",
        )
    removed = forget_source(
        location,
        job.dataset.name,
        source_url,
        keep_sources={digest(source.model_dump_json()) for source in sources},
        limits=job.limits,
    )
    return {"schema_version": 1, "source_url": source_url, "removed": removed}
