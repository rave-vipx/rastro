"""Dataset schema 4, deliberately separate from recipe memory (ADR 0017)."""

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory

from rastro.adapters.sqlite_files import create_private, restrict
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

CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS run_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "dataset TEXT NOT NULL, run_id TEXT NOT NULL, source_url TEXT NOT NULL, host TEXT NOT NULL, "
    "strategy TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
    "outcome TEXT NOT NULL, http_status INTEGER, requests INTEGER NOT NULL, "
    "retries INTEGER NOT NULL, bytes INTEGER NOT NULL, network_seconds REAL NOT NULL, "
    "processing_seconds REAL NOT NULL, render_seconds REAL NOT NULL, model_calls INTEGER NOT NULL, "
    "model_tokens INTEGER NOT NULL, records INTEGER NOT NULL, required_fields_ok INTEGER NOT NULL, "
    "fields_validated INTEGER NOT NULL)"
)
# Schema 4: nullable, so schema 3 rows keep "not recorded" rather than a made-up 0.
ADDED_COLUMNS = (
    ("rejected", "INTEGER"),
    ("cap", "TEXT"),
    ("cap_value", "INTEGER"),
    ("requested_url", "TEXT"),  # the configured URL of a redirected source
)
CREATE_INDEX = "CREATE INDEX IF NOT EXISTS run_metrics_dataset ON run_metrics (dataset, id)"


class ConcurrentDatasetCreation(Exception):
    """Another writer published first; retry against its committed database."""


def _markers(db: sqlite3.Connection) -> tuple[int, int]:
    return (
        db.execute("PRAGMA user_version").fetchone()[0],
        db.execute("PRAGMA application_id").fetchone()[0],
    )


def has_validators(db: sqlite3.Connection) -> bool:
    """Whether the validators table exists (schema 1 files have none until written)."""
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE name = 'dataset_validators'").fetchone()
        is not None
    )


def metric_columns(db: sqlite3.Connection) -> set[str]:
    """Column names of ``run_metrics``; empty when the table does not exist."""
    return {row[1] for row in db.execute("PRAGMA table_info(run_metrics)")}


def _v1(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE dataset_records (dataset TEXT, key TEXT, payload TEXT NOT NULL, "
        "PRIMARY KEY(dataset, key)) WITHOUT ROWID"
    )
    db.execute(
        "CREATE TABLE dataset_sources (dataset TEXT, source TEXT, payload TEXT NOT NULL, "
        "PRIMARY KEY(dataset, source)) WITHOUT ROWID"
    )


def _v2(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS dataset_validators (dataset TEXT, source TEXT, "
        "origin TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, "
        "PRIMARY KEY(dataset, source)) WITHOUT ROWID"
    )


def _v3(db: sqlite3.Connection) -> None:
    db.execute(CREATE_TABLE)  # schema 3 (OBS-003)
    db.execute(CREATE_INDEX)


def _v4(db: sqlite3.Connection) -> None:
    _v2(db)
    _v3(db)
    present = metric_columns(db)
    for name, kind in ADDED_COLUMNS:
        if name not in present:
            db.execute(f"ALTER TABLE run_metrics ADD COLUMN {name} {kind}")
    db.execute(CREATE_INDEX)


# Index i migrates schema i to schema i + 1. Never reorder released steps.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_v1, _v2, _v3, _v4)
SCHEMA_VERSION = len(MIGRATIONS)
KNOWN_VERSIONS = tuple(range(1, SCHEMA_VERSION + 1))


def check_schema(db: sqlite3.Connection) -> None:
    """Reads accept schema 1 to 4 as they are; only the write path migrates forward."""
    if _markers(db) not in {(v, APPLICATION_ID) for v in KNOWN_VERSIONS}:
        raise DatasetStorageError("Foreign or newer database; dataset read refused")


def migrate(db: sqlite3.Connection, *, to: int = SCHEMA_VERSION) -> tuple[int, int]:
    """Migrate forward within the caller's transaction; never commit independently."""
    if isinstance(to, bool) or not isinstance(to, int) or to not in KNOWN_VERSIONS:
        raise DatasetStorageError("Unknown dataset schema version; dataset migration refused")
    version, app = _markers(db)
    if (version, app) == (to, APPLICATION_ID):
        return version, to
    if app != APPLICATION_ID or version not in KNOWN_VERSIONS:
        if version or app or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            raise DatasetStorageError("Foreign or newer database; dataset migration refused")
        version = 0
    if to < version:
        raise DatasetStorageError("Dataset schema migrations are forward-only; downgrade refused")
    for step in MIGRATIONS[version:to]:
        step(db)
    db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    db.execute(f"PRAGMA user_version = {to}")
    return version, to


@contextmanager
def _transaction(
    path: Path, limits: Limits, *, write: bool, auto_migrate: bool = True
) -> Iterator[sqlite3.Connection]:
    """Reads open read-only and never migrate; writes create and migrate forward."""
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
                if auto_migrate:
                    migrate(db)
            else:
                check_schema(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


@contextmanager
def connection(
    path: Path, limits: Limits, *, write: bool = False, auto_migrate: bool = True
) -> Iterator[sqlite3.Connection]:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise DatasetStorageError("Dataset paths must not contain symlinks")
    with ExitStack() as cleanup:
        target = path
        if write and not path.exists():
            staging = cleanup.enter_context(TemporaryDirectory(dir=path.parent))
            target = Path(staging) / "dataset.sqlite"
            create_private(target)
        else:
            restrict(path)
        with _transaction(target, limits, write=write, auto_migrate=auto_migrate) as db:
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
    detail: str | None = None,
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
        detail=detail,
    )
    db.execute(
        "INSERT OR REPLACE INTO dataset_sources VALUES (?, ?, ?)",
        (dataset, source, status.model_dump_json()),
    )
