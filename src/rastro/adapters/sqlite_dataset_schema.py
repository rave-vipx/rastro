"""Dataset schema 1, deliberately separate from recipe memory (ADR 0017)."""

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory

from rastro.domain.dataset import DatasetStorageError, SourceStatus
from rastro.domain.sources import Limits, ResourceLimitError


def safe[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        for _ in range(2):
            try:
                return operation(*args, **kwargs)
            except ConcurrentDatasetCreation:
                continue
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
        raise DatasetStorageError("Concurrent dataset creation did not settle")

    return call


APPLICATION_ID = 0x52534453  # RSDS
SCHEMA_VERSION = 1


class ConcurrentDatasetCreation(Exception):
    """Another writer published first; retry against its committed database."""


def _markers(db: sqlite3.Connection) -> tuple[int, int]:
    return (
        db.execute("PRAGMA user_version").fetchone()[0],
        db.execute("PRAGMA application_id").fetchone()[0],
    )


def check_schema(db: sqlite3.Connection) -> None:
    if _markers(db) != (SCHEMA_VERSION, APPLICATION_ID):
        raise DatasetStorageError("Foreign or newer database; dataset read refused")


def migrate(db: sqlite3.Connection) -> None:
    """Initialize within the caller's transaction; never commit independently."""
    version, app = _markers(db)
    if (version, app) == (SCHEMA_VERSION, APPLICATION_ID):
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


@contextmanager
def _transaction(path: Path, limits: Limits, *, write: bool) -> Iterator[sqlite3.Connection]:
    mode = "rwc" if write else "ro"
    with closing(
        sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=5, isolation_level=None)
    ) as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_json_bytes)
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            if write:
                db.execute("PRAGMA secure_delete = ON")
                migrate(db)
            else:
                check_schema(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


@contextmanager
def connection(path: Path, limits: Limits, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise DatasetStorageError("Dataset paths must not contain symlinks")
    with ExitStack() as cleanup:
        target = path
        if write and not path.exists():
            staging = cleanup.enter_context(TemporaryDirectory(dir=path.parent))
            target = Path(staging) / "dataset.sqlite"
        with _transaction(target, limits, write=write) as db:
            yield db
        if target != path:
            try:
                os.link(target, path)
            except FileExistsError:
                raise ConcurrentDatasetCreation from None


def last_attempt(db: sqlite3.Connection, dataset: str, source: str) -> datetime | None:
    row = db.execute(
        "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
        (dataset, source),
    ).fetchone()
    return SourceStatus.model_validate_json(row[0]).last_attempt if row else None


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
        # The clock went backwards: clamp to the last recorded attempt (docs/operations.md).
        at = old.last_attempt
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
