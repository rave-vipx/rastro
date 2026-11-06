"""Dataset schema 1, deliberately separate from recipe memory (ADR 0017)."""

import sqlite3
from collections.abc import Callable
from datetime import datetime
from functools import wraps

from rastro.domain.dataset import DatasetStorageError, SourceStatus
from rastro.domain.sources import ResourceLimitError


def safe[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return operation(*args, **kwargs)
        except (
            sqlite3.Error,
            OSError,
            ValueError,
            TypeError,
            RecursionError,
            AttributeError,
            OverflowError,
            ResourceLimitError,
        ):
            raise DatasetStorageError("Dataset operation failed") from None

    return call


APPLICATION_ID = 0x52534453  # RSDS
SCHEMA_VERSION = 1


def migrate(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        app = db.execute("PRAGMA application_id").fetchone()[0]
        if (version, app) == (SCHEMA_VERSION, APPLICATION_ID):
            db.commit()
            return
        if version or app or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            raise DatasetStorageError("Foreign or newer database; dataset migration refused")
        db.execute(
            "CREATE TABLE dataset_records (dataset TEXT, key TEXT, payload TEXT NOT NULL, "
            "PRIMARY KEY(dataset, key)) WITHOUT ROWID"
        )
        db.execute(
            "CREATE TABLE dataset_sources (dataset TEXT, source TEXT, payload TEXT NOT NULL, "
            "PRIMARY KEY(dataset, source)) WITHOUT ROWID"
        )
        db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def update_status(
    db: sqlite3.Connection,
    dataset: str,
    source: str,
    at: datetime,
    complete: bool,
    reason: str | None,
) -> None:
    row = db.execute(
        "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
        (dataset, source),
    ).fetchone()
    old = SourceStatus.model_validate_json(row[0]) if row else None
    if old and at < old.last_attempt:
        raise DatasetStorageError("Out-of-order source observation")
    status = SourceStatus(
        source_url=source,
        last_success=at if complete else old.last_success if old else None,
        last_attempt=at,
        stale=not complete,
        reason=reason,
    )
    db.execute(
        "INSERT OR REPLACE INTO dataset_sources VALUES (?, ?, ?)",
        (dataset, source, status.model_dump_json()),
    )
