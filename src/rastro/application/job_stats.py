"""`rastro job stats`: aggregate a job dataset's per-source run metrics (OBS-003..006)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.job_dataset import dataset_location
from rastro.application.job_loader import load_job
from rastro.domain.metrics import Grouping, parse_since, summarize
from rastro.domain.models import ConfigurationError

GROUPINGS: tuple[Grouping, ...] = ("source", "host", "strategy", "day")


def job_stats(
    path: str | Path,
    *,
    since: str | None = None,
    by: Grouping = "source",
    allow_absolute_paths: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Machine-readable stats (``schema_version`` 1); an empty history says ``no_runs``.

    Raises ``ConfigurationError`` for a bad ``since``/``by``, a job without ``[dataset]`` and
    for Postgres-backed datasets, which keep no metrics yet.
    """
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    if job.dataset is None:
        raise ConfigurationError("This command requires a [dataset] block in the job file")
    if job.dataset.backend != "sqlite":
        raise ConfigurationError("stats need the local SQLite dataset")
    if by not in GROUPINGS:
        raise ConfigurationError(f"--by must be one of {', '.join(GROUPINGS)}")
    now = now or datetime.now(UTC)
    cutoff = None
    if since is not None:
        try:
            cutoff = parse_since(since, now)
        except ValueError:
            raise ConfigurationError("--since must look like 7d or 12h", location="since") from None
    location = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    rows = (
        SQLiteDatasetStore(location, limits=job.limits).run_metrics(job.dataset.name, cutoff)
        if location.is_file()
        else ()
    )
    if not rows:
        return {"schema_version": 1, "groups": [], "reason": "no_runs"}
    return {
        "schema_version": 1,
        "dataset": job.dataset.name,
        "by": by,
        "since": since,
        "generated_at": now.isoformat(),
        "groups": summarize(rows, by, now),
    }
