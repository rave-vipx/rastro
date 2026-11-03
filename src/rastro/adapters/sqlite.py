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

from rastro.adapters.sqlite_lifecycle import (
    apply_changes,
    drop_orphan_events,
    read_history,
    serving_status,
    version_status,
)
from rastro.adapters.sqlite_schema import migrate
from rastro.domain.lifecycle import RecipeState, plan_creation, plan_rollback, plan_transition
from rastro.domain.lifecycle_history import LifecycleHistory, check_reason
from rastro.domain.memory import (
    RecipeConflictError,
    RecipeKey,
    RecipeMetadata,
    RecipeStorageError,
    StoredRecipe,
    utc_timestamp,
)
from rastro.domain.models import ConfigurationError, Recipe

_MAX_PAYLOAD = 600_000
_ROW = (
    "SELECT version, payload, selected_index, created_at, last_success_at, successful_runs, "
    "state FROM recipes WHERE key = ?"
)


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
                migrate(db)
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        except (sqlite3.Error, OSError, ValidationError, ValueError, TypeError, RecursionError):
            raise RecipeStorageError("Recipe storage operation failed") from None

    @classmethod
    def _read(cls, db: sqlite3.Connection, key: RecipeKey, version: int) -> StoredRecipe:
        stored = cls._find(db, key, version)
        if stored is None:
            raise RecipeStorageError("Recipe version disappeared during a write")
        return stored

    @staticmethod
    def _find(db: sqlite3.Connection, key: RecipeKey, version: int) -> StoredRecipe | None:
        row = db.execute(f"{_ROW} AND version = ?", (key.canonical(), version)).fetchone()
        if row is None:
            return None
        version, payload, index, created, success, count, state = row
        stored = StoredRecipe.model_validate(
            dict(
                key=key,
                recipe=json.loads(str(payload)),
                metadata=dict(
                    selected_index=index,
                    created_at=created,
                    last_success_at=success,
                    successful_runs=count,
                    state=state,
                ),
            )
        )
        if stored.recipe.version != version:
            raise RecipeStorageError("Inconsistent recipe version")
        return stored

    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None:
        """Return the pinned version in any state, or else the serving version."""
        self._check_version(version)
        identity = key.canonical()
        with self._connection() as db:
            if version is None:
                serving = serving_status(db, identity)
                if serving is None:
                    return None
                version = serving.version
            return self._find(db, key, version)

    def save_success(
        self,
        key: RecipeKey,
        recipe: Recipe,
        *,
        selected_index: int,
        at: datetime,
        candidate: bool = False,
    ) -> StoredRecipe:
        """Record a validated success. A new version becomes active unless ``candidate``.

        Existing versions only update success history; their state never changes here.
        """
        recipe = Recipe.model_validate(recipe.model_dump())
        timestamp = utc_timestamp(at)
        StoredRecipe(  # Validate the selection before touching storage.
            key=key,
            recipe=recipe,
            metadata=RecipeMetadata(
                selected_index=selected_index, created_at=at, last_success_at=at, successful_runs=1
            ),
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
            # Compare validated definitions so new optional defaults do not conflict with M3 JSON.
            if existing is not None and Recipe.model_validate_json(existing[0]) != recipe:
                raise RecipeConflictError("Recipe definition changed; increment its version")
            db.execute(
                "INSERT INTO recipes (key, version, payload, selected_index, created_at, "
                "last_success_at, successful_runs, state) VALUES (?, ?, ?, ?, ?, ?, 1, "
                "'candidate') ON CONFLICT(key, version) DO UPDATE SET "
                "selected_index = CASE WHEN excluded.last_success_at >= last_success_at "
                "THEN excluded.selected_index ELSE selected_index END, "
                "created_at = MIN(created_at, excluded.created_at), "
                "last_success_at = MAX(last_success_at, excluded.last_success_at), "
                "successful_runs = successful_runs + 1",
                (identity, recipe.version, payload, selected_index, timestamp, timestamp),
            )
            if existing is None:
                changes = plan_creation(
                    recipe.version, candidate=candidate, serving=serving_status(db, identity)
                )
                reason = "Stored candidate" if candidate else "Validated extraction succeeded"
                apply_changes(db, identity, changes, reason=reason, at=at)
            return self._read(db, key, recipe.version)

    def transition(
        self, key: RecipeKey, version: int, state: RecipeState, *, reason: str, at: datetime
    ) -> StoredRecipe:
        """Promote, reject, degrade, recover or retire one version.

        Promotion retires the version that was serving. Raises
        ``LifecycleTransitionError`` for transitions the state machine forbids.
        """
        self._check_version(version, required=True)
        target_state = self._state(state)
        reason = self._reason(reason)
        utc_timestamp(at)  # Reject naive timestamps before opening storage.
        identity = key.canonical()
        with self._connection(write=True) as db:
            target = version_status(db, identity, version)
            changes = plan_transition(target, target_state, serving=serving_status(db, identity))
            apply_changes(db, identity, changes, reason=reason, at=at)
            return self._read(db, key, version)

    def rollback(self, key: RecipeKey, version: int, *, reason: str, at: datetime) -> StoredRecipe:
        """Return a retired version that previously served to active service (REPAIR-006)."""
        self._check_version(version, required=True)
        reason = self._reason(reason)
        utc_timestamp(at)  # Reject naive timestamps before opening storage.
        identity = key.canonical()
        with self._connection(write=True) as db:
            target = version_status(db, identity, version)
            changes = plan_rollback(target, serving=serving_status(db, identity))
            apply_changes(db, identity, changes, reason=reason, at=at)
            return self._read(db, key, version)

    def history(self, key: RecipeKey) -> LifecycleHistory:
        """Return version states and ordered lifecycle events for ``key``."""
        with self._connection() as db:
            return read_history(db, key)

    @staticmethod
    def _check_version(version: int | None, *, required: bool = False) -> None:
        if version is None and not required:
            return
        if type(version) is not int or not 1 <= version <= 2_147_483_647:
            raise ConfigurationError("Recipe version is out of range")

    @staticmethod
    def _state(state: RecipeState) -> RecipeState:
        try:
            return RecipeState(state)
        except ValueError:
            raise ConfigurationError("Unknown recipe lifecycle state") from None

    @staticmethod
    def _reason(reason: str) -> str:
        try:
            return check_reason(reason)
        except ValidationError:
            raise ConfigurationError(
                "Lifecycle reasons must be 1-500 printable characters"
            ) from None

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int:
        self._check_version(version)
        with self._connection(write=True) as db:
            count = db.execute(
                "DELETE FROM recipes WHERE key = ? AND (? IS NULL OR version = ?)",
                (key.canonical(), version, version),
            ).rowcount
            drop_orphan_events(db)
            return count

    def prune(self, *, before: datetime) -> int:
        cutoff = utc_timestamp(before)
        with self._connection(write=True) as db:
            count = db.execute("DELETE FROM recipes WHERE last_success_at < ?", (cutoff,)).rowcount
            drop_orphan_events(db)
            return count
