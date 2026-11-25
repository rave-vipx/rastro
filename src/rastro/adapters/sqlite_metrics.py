"""``run_metrics`` rows of the SQLite dataset; counts and timings only."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from rastro.adapters.sqlite_dataset_schema import connection, metric_columns, safe
from rastro.domain.dataset import DatasetRecordKey
from rastro.domain.metrics import RETENTION_ROWS, RunMetric
from rastro.domain.sources import Limits

COLUMNS = (
    "run_id, source_url, host, strategy, started_at, finished_at, outcome, http_status, "
    "requests, retries, bytes, network_seconds, processing_seconds, render_seconds, "
    "model_calls, model_tokens, records, required_fields_ok, fields_validated, "
    "rejected, cap, cap_value, requested_url"
)
NAMES = tuple(name.strip() for name in COLUMNS.split(","))


def has_metrics(db: sqlite3.Connection) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE name = 'run_metrics'").fetchone() is not None
    )


class SQLiteMetricsMixin:
    """Gives ``SQLiteDatasetStore`` the ``RunMetricsRecorder`` methods."""

    _path: Path
    _limits: Limits
    _metrics_retention: int = RETENTION_ROWS

    @safe
    def record_run_metrics(self, dataset: str, rows: Sequence[RunMetric]) -> None:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        marks = ", ".join("?" * (len(NAMES) + 1))
        with connection(self._path, self._limits, write=True) as db:
            db.executemany(
                f"INSERT INTO run_metrics (dataset, {COLUMNS}) VALUES ({marks})",
                [(dataset, *_values(row)) for row in rows],
            )
            db.execute(
                "DELETE FROM run_metrics WHERE dataset = ? AND id <= "
                "(SELECT id FROM run_metrics WHERE dataset = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (dataset, dataset, self._metrics_retention),
            )

    @safe
    def run_metrics(self, dataset: str, since: datetime | None = None) -> tuple[RunMetric, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with connection(self._path, self._limits) as db:
            if not has_metrics(db):
                return ()
            # A schema 3 file is read as is: its rows lack the schema 4 columns.
            present = metric_columns(db)
            chosen = ", ".join(name if name in present else "NULL" for name in NAMES)
            cursor = db.execute(
                f"SELECT {chosen} FROM run_metrics WHERE dataset = ? ORDER BY id LIMIT ?",
                (dataset, self._metrics_retention),
            )
            rows = tuple(_row(values) for values in cursor)
        return rows if since is None else tuple(r for r in rows if r.finished_at >= since)


def _values(row: RunMetric) -> tuple[object, ...]:
    data = row.model_dump()
    data["started_at"] = row.started_at.isoformat()
    data["finished_at"] = row.finished_at.isoformat()
    return tuple(data.get(name) for name in NAMES)  # model_dump omits a None requested_url


def _row(values: Sequence[object]) -> RunMetric:
    return RunMetric.model_validate(dict(zip(NAMES, values, strict=True)))
