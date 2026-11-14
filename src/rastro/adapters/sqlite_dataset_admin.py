"""Operator maintenance of a dataset file: acknowledge drift, forget a source (ADR 0022)."""

from pathlib import Path

from rastro.adapters import sqlite_drift_open as drift_open
from rastro.adapters.sqlite_dataset_schema import connection, safe
from rastro.adapters.sqlite_drift import _migrate, _version
from rastro.domain.dataset import DatasetRecordKey
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.models import Limits


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
