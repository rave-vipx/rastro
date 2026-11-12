"""Drift history in the dataset file, with an independent transactional version row."""

import hashlib
import json
import sqlite3
from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_schema import connection, safe
from rastro.adapters.structured import load_json
from rastro.domain.dataset import DatasetStorageError, SourceStatus
from rastro.domain.drift import DriftAssessment, DriftThresholds, StructuralSnapshot, assess_drift
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.memory import utc_timestamp
from rastro.domain.models import Limits, Outcome


def _has_schema(db: sqlite3.Connection) -> bool:
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'drift_*'"
        )
    }
    if tables:
        if tables != {"drift_schema", "drift_runs"} or db.execute(
            "SELECT version FROM drift_schema WHERE id = 1"
        ).fetchall() != [(1,)]:
            raise DatasetStorageError("Foreign or newer drift schema")
        return True
    return False


def _migrate(db: sqlite3.Connection) -> None:
    if _has_schema(db):
        return
    db.execute("CREATE TABLE drift_schema (id INTEGER PRIMARY KEY CHECK(id = 1), version INTEGER)")
    db.execute("INSERT INTO drift_schema VALUES (1, 1)")
    db.execute(
        "CREATE TABLE drift_runs (dataset TEXT, context TEXT, run TEXT, observed TEXT NOT NULL, "
        "successful INTEGER NOT NULL, snapshot TEXT NOT NULL, assessment TEXT NOT NULL, "
        "PRIMARY KEY(dataset, context, run)) WITHOUT ROWID"
    )
    db.execute("CREATE INDEX drift_baseline ON drift_runs(dataset, context, successful, observed)")


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
    ) -> StructuralSnapshot | None:
        row = db.execute(
            "SELECT snapshot FROM drift_runs WHERE dataset = ? AND context = ? AND successful = 1 "
            "ORDER BY observed DESC, run DESC LIMIT 1",
            (dataset, _context(snapshot)),
        ).fetchone()
        if row is None:
            return None
        baseline = StructuralSnapshot.model_validate(load_json(row[0], self._limits))
        if baseline.context != snapshot.context or baseline.status != Outcome.SUCCESS:
            raise DatasetStorageError("Invalid drift baseline context")
        return baseline

    @safe
    def baseline(self, dataset: str, snapshot: StructuralSnapshot) -> StructuralSnapshot | None:
        """Return the last successful snapshot in this source/schema/region/egress context."""
        snapshot = self._validate(dataset, snapshot)
        with connection(self._path, self._limits) as db:
            return self._baseline(db, dataset, snapshot) if _has_schema(db) else None

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
                raise DatasetStorageError("Out-of-order drift observation")
            assessment = assess_drift(
                self._baseline(db, dataset, snapshot),
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
            return assessment


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
