"""Local recipe memory using short-lived connections and atomic versioned writes."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from rastro.domain.memory import (
    RecipeConflictError,
    RecipeKey,
    RecipeStorageError,
    StoredRecipe,
    utc_timestamp,
)
from rastro.domain.models import ConfigurationError, Recipe

_APPLICATION_ID = 0x52535452  # RSTR
_SCHEMA_VERSION = 1
_MAX_PAYLOAD = 600_000


class SQLiteRecipeRepository:
    """Lazy file-backed repository. Each operation closes its own connection.

    The file and its parent directory must be trusted and private. No URL, HTML,
    extracted record or JSON Schema document is stored. Call from a worker thread
    when using this synchronous port in asynchronous applications.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        if not str(path) or str(path) == ":memory:" or str(path).startswith("file:"):
            raise ConfigurationError("Recipe memory requires a filesystem path")
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ConfigurationError("Storage timeout must be between 0 and 30 seconds")
        self._path = Path(path).absolute()
        self._timeout = timeout

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            with closing(
                sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)
            ) as db:
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("PRAGMA secure_delete = ON")
                self._migrate(db)
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        except (sqlite3.Error, OSError, ValidationError, ValueError, TypeError, RecursionError):
            raise RecipeStorageError("Recipe storage operation failed") from None

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        app = db.execute("PRAGMA application_id").fetchone()[0]
        if version == _SCHEMA_VERSION and app == _APPLICATION_ID:
            return
        # Recheck inside the write transaction: two processes may create the file together.
        db.execute("BEGIN IMMEDIATE")
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            app = db.execute("PRAGMA application_id").fetchone()[0]
            if version == _SCHEMA_VERSION and app == _APPLICATION_ID:
                db.commit()
                return
            if (
                version != 0
                or app != 0
                or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            ):
                raise RecipeStorageError("Unrecognized recipe database; no migration applied")
            # Migration 0 -> 1: all DDL and version markers commit atomically.
            db.execute("""CREATE TABLE recipes (
                key TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                payload TEXT NOT NULL,
                selected_index INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_success_at TEXT NOT NULL,
                successful_runs INTEGER NOT NULL CHECK (successful_runs > 0),
                PRIMARY KEY (key, version)
            ) WITHOUT ROWID""")
            db.execute("CREATE INDEX recipe_retention ON recipes(last_success_at)")
            db.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        except BaseException:
            db.rollback()
            raise
        db.commit()

    @staticmethod
    def _decode(key: RecipeKey, row: tuple[object, ...]) -> StoredRecipe:
        version, payload, index, created, success, count = row
        stored = StoredRecipe.model_validate(
            dict(
                key=key,
                recipe=json.loads(str(payload)),
                selected_index=index,
                created_at=created,
                last_success_at=success,
                successful_runs=count,
            )
        )
        if stored.recipe.version != version:
            raise RecipeStorageError("Inconsistent recipe version")
        return stored

    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None:
        self._check_version(version)
        with self._connection() as db:
            row = db.execute(
                "SELECT version, payload, selected_index, created_at, last_success_at, "
                "successful_runs FROM recipes WHERE key = ? AND (? IS NULL OR version = ?) "
                "ORDER BY version DESC LIMIT 1",
                (key.canonical(), version, version),
            ).fetchone()
            return None if row is None else self._decode(key, row)

    def save_success(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe:
        recipe = Recipe.model_validate(recipe.model_dump())
        timestamp = utc_timestamp(at)
        snapshot = StoredRecipe(
            key=key,
            recipe=recipe,
            selected_index=selected_index,
            created_at=at,
            last_success_at=at,
            successful_runs=1,
        )
        payload = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if len(payload.encode()) > _MAX_PAYLOAD:
            raise RecipeStorageError("Recipe payload exceeds storage budget")
        identity = key.canonical()
        with self._connection(write=True) as db:
            existing = db.execute(
                "SELECT payload FROM recipes WHERE key = ? AND version = ?",
                (identity, recipe.version),
            ).fetchone()
            if existing is not None and existing[0] != payload:
                raise RecipeConflictError("Recipe definition changed; increment its version")
            db.execute(
                "INSERT INTO recipes VALUES (?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(key, version) DO UPDATE SET "
                "selected_index = CASE WHEN excluded.last_success_at >= last_success_at "
                "THEN excluded.selected_index ELSE selected_index END, "
                "created_at = MIN(created_at, excluded.created_at), "
                "last_success_at = MAX(last_success_at, excluded.last_success_at), "
                "successful_runs = successful_runs + 1",
                (identity, recipe.version, payload, snapshot.selected_index, timestamp, timestamp),
            )
            row = db.execute(
                "SELECT version, payload, selected_index, created_at, last_success_at, "
                "successful_runs FROM recipes WHERE key = ? AND version = ?",
                (identity, recipe.version),
            ).fetchone()
            return self._decode(key, row)

    @staticmethod
    def _check_version(version: int | None) -> None:
        if version is not None and (type(version) is not int or not 1 <= version <= 2_147_483_647):
            raise ConfigurationError("Recipe version is out of range")

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int:
        self._check_version(version)
        with self._connection(write=True) as db:
            return db.execute(
                "DELETE FROM recipes WHERE key = ? AND (? IS NULL OR version = ?)",
                (key.canonical(), version, version),
            ).rowcount

    def prune(self, *, before: datetime) -> int:
        cutoff = utc_timestamp(before)
        with self._connection(write=True) as db:
            return db.execute("DELETE FROM recipes WHERE last_success_at < ?", (cutoff,)).rowcount
