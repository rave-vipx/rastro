"""Operator maintenance of a dataset file: acknowledge drift, forget a source (ADR 0023)."""

import sqlite3
from collections.abc import Collection
from pathlib import Path

from rastro.adapters import sqlite_drift_open as drift_open
from rastro.adapters.sqlite_dataset_schema import connection, safe
from rastro.adapters.sqlite_drift import _migrate, _version
from rastro.adapters.structured import load_json
from rastro.domain.dataset import DatasetRecordKey
from rastro.domain.drift import StructuralSnapshot
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.models import Limits

_REMOVED = ("dataset_sources", "drift_runs", "open_drift", "relocation_samples")


def _path(path: str | Path) -> Path:
    return Path(validate_job_path_syntax(str(path))).absolute()


@safe
def acknowledge_drift(
    path: str | Path, dataset: str, source_url: str, *, limits: Limits | None = None
) -> int:
    """Clear the open drift of one source; the next run compares with its latest success."""
    DatasetRecordKey(dataset=dataset, identity=("validation",))
    file = _path(path)
    if not file.exists():
        return 0
    with connection(file, limits or Limits(), write=True) as db:
        return drift_open.clear(db, dataset, source_url) if _version(db) == 2 else 0


@safe
def rebaseline_drift(
    path: str | Path, dataset: str, source_url: str, *, limits: Limits | None = None
) -> None:
    """The serving recipe changed: clear open drift and accept the next success as baseline."""
    DatasetRecordKey(dataset=dataset, identity=("validation",))
    file = _path(path)
    if not file.exists():
        return
    with connection(file, limits or Limits(), write=True) as db:
        if _version(db) is None:
            return
        _migrate(db)
        drift_open.clear(db, dataset, source_url)
        drift_open.mark_rebase(db, dataset, source_url)


@safe
def forget_source(
    path: str | Path,
    dataset: str,
    source_url: str,
    *,
    sample_sources: Collection[str],
    limits: Limits | None = None,
) -> dict[str, int]:
    """Delete one origin's status, drift history, open drift and samples in one transaction.

    Relocation samples are keyed by a digest of the full source (query and region
    included), so only samples whose digest is in ``sample_sources`` are removed; another
    job sharing the dataset keeps its samples. Stored records and their provenance are kept.
    """
    DatasetRecordKey(dataset=dataset, identity=("validation",))
    limits = limits or Limits()
    removed = dict.fromkeys(_REMOVED, 0)
    file = _path(path)
    if not file.exists():
        return removed
    with connection(file, limits, write=True) as db:
        removed["dataset_sources"] = db.execute(
            "DELETE FROM dataset_sources WHERE dataset = ? AND source = ?", (dataset, source_url)
        ).rowcount
        version = _version(db)
        if version is not None:
            removed["drift_runs"] = _forget_runs(db, dataset, source_url, limits)
        if version == 2:
            removed["open_drift"] = drift_open.clear(db, dataset, source_url)
            drift_open.take_rebase(db, dataset, source_url)
        removed["relocation_samples"] = _forget_samples(db, dataset, sample_sources)
    return removed


def _forget_runs(db: sqlite3.Connection, dataset: str, source_url: str, limits: Limits) -> int:
    runs = [
        (dataset, context, run)
        for context, run, snapshot in db.execute(
            "SELECT context, run, snapshot FROM drift_runs WHERE dataset = ?", (dataset,)
        ).fetchall()
        if StructuralSnapshot.model_validate(load_json(snapshot, limits)).source_url == source_url
    ]
    db.executemany("DELETE FROM drift_runs WHERE dataset = ? AND context = ? AND run = ?", runs)
    return len(runs)


def _forget_samples(db: sqlite3.Connection, dataset: str, sources: Collection[str]) -> int:
    found = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'relocation_samples'"
    ).fetchone()
    if not found:
        return 0
    return sum(
        db.execute(
            "DELETE FROM relocation_samples WHERE dataset = ? AND source = ?", (dataset, source)
        ).rowcount
        for source in sorted(sources)
    )
