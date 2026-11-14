"""Drift history in the dataset file, with an independent transactional version row."""

import hashlib
import json
import sqlite3
from pathlib import Path

from rastro.adapters import sqlite_drift_open as drift_open
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_schema import connection, safe
from rastro.adapters.structured import load_json
from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError, SourceStatus
from rastro.domain.drift import DriftAssessment, DriftThresholds, StructuralSnapshot, assess_drift
from rastro.domain.health import OpenDrift
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.memory import utc_timestamp
from rastro.domain.models import Limits, Outcome

SCHEMA_VERSION = 2
_TABLES = {
    1: {"drift_schema", "drift_runs"},
    2: {"drift_schema", "drift_runs", "drift_open", "drift_rebase"},
}


def _version(db: sqlite3.Connection) -> int | None:
    """The drift schema version, ``None`` before any drift write; refuses unknown layouts."""
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'drift_*'"
        )
    }
    if not tables:
        return None
    rows = (
        db.execute("SELECT version FROM drift_schema WHERE id = 1").fetchall()
        if "drift_schema" in tables
        else []
    )
    version = rows[0][0] if len(rows) == 1 else None
    if version not in _TABLES or tables != _TABLES[version]:
        raise DatasetStorageError("Foreign or newer drift schema")
    return int(version)


def _has_schema(db: sqlite3.Connection) -> bool:
    return _version(db) is not None


def _migrate(db: sqlite3.Connection) -> None:
    """Forward-only, idempotent, in the caller's write transaction; 1 -> 2 adds open drift."""
    version = _version(db)
    if version is None:
        db.execute(
            "CREATE TABLE drift_schema (id INTEGER PRIMARY KEY CHECK(id = 1), version INTEGER)"
        )
        db.execute("INSERT INTO drift_schema VALUES (1, 1)")
        db.execute(
            "CREATE TABLE drift_runs (dataset TEXT, context TEXT, run TEXT, "
            "observed TEXT NOT NULL, successful INTEGER NOT NULL, snapshot TEXT NOT NULL, "
            "assessment TEXT NOT NULL, "
            "PRIMARY KEY(dataset, context, run)) WITHOUT ROWID"
        )
        db.execute(
            "CREATE INDEX drift_baseline ON drift_runs(dataset, context, successful, observed)"
        )
        version = 1
    if version == 1:
        drift_open.create_table(db)
        db.execute("UPDATE drift_schema SET version = 2 WHERE id = 1")


def _context(snapshot: StructuralSnapshot) -> str:
    return hashlib.sha256(json.dumps(snapshot.context).encode()).hexdigest()


class SQLiteDriftMonitor:
    @safe
    def __init__(
        self,
        path: str | Path,
        *,
        limits: Limits | None = None,
        thresholds: DriftThresholds | None = None,
    ) -> None:
        self._path = Path(validate_job_path_syntax(str(path))).absolute()
        self._limits = limits or Limits()
        self._thresholds = DriftThresholds.model_validate(
            (thresholds or DriftThresholds()).model_dump()
        )

    def _baseline(
        self,
        db: sqlite3.Connection,
        dataset: str,
        snapshot: StructuralSnapshot,
    ) -> tuple[str, StructuralSnapshot] | None:
        """The last successful run, or the run pinned while drift is open in this context."""
        context = _context(snapshot)
        pin = drift_open.pinned(db, dataset, context) if _version(db) == 2 else None
        if pin is None:
            row = db.execute(
                "SELECT run, snapshot FROM drift_runs WHERE dataset = ? AND context = ? "
                "AND successful = 1 ORDER BY observed DESC, run DESC LIMIT 1",
                (dataset, context),
            ).fetchone()
        elif pin[0] is None:
            return None
        else:
            row = db.execute(
                "SELECT run, snapshot FROM drift_runs WHERE dataset = ? AND context = ? "
                "AND run = ? AND successful = 1",
                (dataset, context, pin[0]),
            ).fetchone()
            if row is None:
                raise DatasetStorageError("Open drift baseline is missing")
        if row is None:
            return None
        baseline = StructuralSnapshot.model_validate(load_json(row[1], self._limits))
        if baseline.context != snapshot.context or baseline.status != Outcome.SUCCESS:
            raise DatasetStorageError("Invalid drift baseline context")
        return row[0], baseline

    @safe
    def baseline(self, dataset: str, snapshot: StructuralSnapshot) -> StructuralSnapshot | None:
        """Return the last successful snapshot in this source/schema/region/egress context."""
        snapshot = self._validate(dataset, snapshot)
        with connection(self._path, self._limits) as db:
            found = self._baseline(db, dataset, snapshot) if _has_schema(db) else None
            return found[1] if found else None

    def _validate(self, dataset: str, snapshot: StructuralSnapshot) -> StructuralSnapshot:
        snapshot = StructuralSnapshot.model_validate(
            load_json(snapshot.model_dump_json(), self._limits)
        )
        SQLiteDatasetStore._validate(dataset, snapshot.source_url, snapshot.observed_at)
        return snapshot

    @safe
    def assess(self, dataset: str, snapshot: StructuralSnapshot) -> DriftAssessment:
        snapshot = self._validate(dataset, snapshot)
        observed = utc_timestamp(snapshot.observed_at)
        key = (dataset, _context(snapshot), snapshot.run_id)
        with connection(self._path, self._limits, write=True) as db:
            _migrate(db)
            previous = db.execute(
                "SELECT snapshot, assessment FROM drift_runs WHERE dataset = ? AND context = ? "
                "AND run = ?",
                key,
            ).fetchone()
            if previous:
                if previous[0] != snapshot.model_dump_json():
                    raise DatasetStorageError("Conflicting drift run identifier")
                return DriftAssessment.model_validate(load_json(previous[1], self._limits))
            last = db.execute(
                "SELECT MAX(observed) FROM drift_runs WHERE dataset = ? AND context = ?",
                key[:2],
            ).fetchone()[0]
            if last is not None and observed < last:
                observed = last  # The clock went backwards: clamp (docs/operations.md).
            found = self._baseline(db, dataset, snapshot)
            assessment = assess_drift(
                found[1] if found else None,
                snapshot,
                thresholds=self._thresholds,
                previously_succeeded=_previously_succeeded(db, dataset, snapshot),
            )
            payload = assessment.model_dump_json()
            load_json(payload, self._limits)
            db.execute(
                "INSERT INTO drift_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    observed,
                    int(snapshot.status == Outcome.SUCCESS),
                    snapshot.model_dump_json(),
                    payload,
                ),
            )
            if (
                snapshot.status == Outcome.SUCCESS
                and not drift_open.take_rebase(db, dataset, snapshot.source_url)
                and not drift_open.pinned(db, *key[:2])
            ):
                drift_open.open_drift(
                    db, dataset, key[1], snapshot, assessment, found[0] if found else None
                )
            return assessment

    @safe
    def open_drift(self, dataset: str) -> tuple[OpenDrift, ...]:
        """Unacknowledged elevated or high drift per source; reads never create a file."""
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        if not self._path.exists():
            return ()
        with connection(self._path, self._limits) as db:
            return drift_open.read(db, dataset) if _version(db) == 2 else ()


def _previously_succeeded(
    db: sqlite3.Connection, dataset: str, snapshot: StructuralSnapshot
) -> bool:
    """The dataset recorded a success for this source before this run, with or without drift."""
    has_sources = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dataset_sources'"
    ).fetchone()
    if not has_sources:
        return False
    row = db.execute(
        "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
        (dataset, snapshot.source_url),
    ).fetchone()
    if row is None:
        return False
    status = SourceStatus.model_validate_json(row[0])
    return status.last_success is not None and status.last_success < snapshot.observed_at
