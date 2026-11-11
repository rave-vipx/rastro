"""Local recipe memory using short-lived connections and atomic versioned writes."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from rastro.adapters.sqlite_lifecycle import (
    apply_changes,
    delete_events,
    drop_orphan_events,
    next_version,
    read_history,
    serving_status,
    version_status,
)
from rastro.adapters.sqlite_records import (
    check_lifecycle_input,
    check_state,
    check_version,
    encode,
    find,
    find_serving,
    insert_candidate,
)
from rastro.adapters.sqlite_schema import migrate
from rastro.domain.lifecycle import RecipeState, plan_creation, plan_rollback, plan_transition
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import (
    RecipeConflictError,
    RecipeKey,
    RecipeStorageError,
    StoredRecipe,
    utc_timestamp,
)
from rastro.domain.models import ConfigurationError, Recipe


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

    @staticmethod
    def _read(db: sqlite3.Connection, key: RecipeKey, identity: str, version: int) -> StoredRecipe:
        stored = find(db, key, identity, version)
        if stored is None:
            raise RecipeStorageError("Recipe version disappeared during a write")
        return stored

    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None:
        """Return the pinned version in any state, or else the serving version."""
        check_version(version)
        identity = key.canonical()
        with self._connection() as db:
            if version is None:
                return find_serving(db, key, identity)
            return find(db, key, identity, version)

    def next_version(self, key: RecipeKey) -> int:
        """One above the highest stored version of ``key`` in any state, or 1."""
        identity = key.canonical()
        with self._connection() as db:
            return next_version(db, identity)

    def save_success(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe:
        """Record a validated success; a new version higher than the serving one activates.

        Existing versions only update success history; their state never changes here.
        """
        recipe, payload = encode(key, recipe, selected_index=selected_index, at=at)
        timestamp = utc_timestamp(at)
        identity = key.canonical()
        with self._connection(write=True) as db:
            existing = db.execute(
                "SELECT payload FROM recipes WHERE key = ? AND version = ?",
                (identity, recipe.version),
            ).fetchone()
            if existing is None:
                self._create(db, identity, recipe, payload, selected_index, at, candidate=False)
                return self._read(db, key, identity, recipe.version)
            # Compare validated definitions so new optional defaults do not conflict with M3 JSON.
            if Recipe.model_validate_json(existing[0]) != recipe:
                raise RecipeConflictError("Recipe definition changed; increment its version")
            db.execute(
                "UPDATE recipes SET "
                "selected_index = CASE WHEN ? >= last_success_at "
                "THEN ? ELSE selected_index END, "
                "created_at = MIN(created_at, ?), "
                "last_success_at = MAX(last_success_at, ?), "
                "successful_runs = successful_runs + 1 WHERE key = ? AND version = ?",
                (timestamp, selected_index, timestamp, timestamp, identity, recipe.version),
            )
            return self._read(db, key, identity, recipe.version)

    def save_candidate(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe:
        """Store an evaluated proposal as a new candidate version (REC-006).

        Candidates never touch existing versions: any stored version number conflicts.
        """
        recipe, payload = encode(key, recipe, selected_index=selected_index, at=at)
        identity = key.canonical()
        with self._connection(write=True) as db:
            if find(db, key, identity, recipe.version) is not None:
                raise RecipeConflictError("Candidates require a new recipe version")
            self._create(db, identity, recipe, payload, selected_index, at, candidate=True)
            return self._read(db, key, identity, recipe.version)

    @staticmethod
    def _create(
        db: sqlite3.Connection,
        identity: str,
        recipe: Recipe,
        payload: str,
        selected_index: int,
        at: datetime,
        *,
        candidate: bool,
    ) -> None:
        insert_candidate(db, identity, recipe, payload, selected_index=selected_index, at=at)
        changes = plan_creation(
            recipe.version, candidate=candidate, serving=serving_status(db, identity)
        )
        reason = "Stored candidate" if candidate else "Validated extraction succeeded"
        apply_changes(db, identity, changes, reason=reason, at=at)

    def transition(
        self, key: RecipeKey, version: int, state: RecipeState, *, reason: str, at: datetime
    ) -> StoredRecipe:
        """Promote, reject, degrade, recover or retire one version.

        Promotion retires the version that was serving. Raises
        ``LifecycleTransitionError`` for transitions the state machine forbids.
        """
        check_version(version, required=True)
        target_state = check_state(state)
        reason = check_lifecycle_input(reason, at)
        identity = key.canonical()
        with self._connection(write=True) as db:
            target = version_status(db, identity, version)
            changes = plan_transition(target, target_state, serving=serving_status(db, identity))
            apply_changes(db, identity, changes, reason=reason, at=at)
            return self._read(db, key, identity, version)

    def rollback(self, key: RecipeKey, version: int, *, reason: str, at: datetime) -> StoredRecipe:
        """Return a retired version that previously served to active service (REPAIR-006)."""
        check_version(version, required=True)
        reason = check_lifecycle_input(reason, at)
        identity = key.canonical()
        with self._connection(write=True) as db:
            target = version_status(db, identity, version)
            changes = plan_rollback(target, serving=serving_status(db, identity))
            apply_changes(db, identity, changes, reason=reason, at=at)
            return self._read(db, key, identity, version)

    def history(self, key: RecipeKey) -> LifecycleHistory:
        """Return version states and ordered lifecycle events for ``key``."""
        identity = key.canonical()  # Invalid keys fail before any file is created.
        with self._connection() as db:
            return read_history(db, key, identity)

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int:
        check_version(version)
        identity = key.canonical()
        with self._connection(write=True) as db:
            count = db.execute(
                "DELETE FROM recipes WHERE key = ? AND (? IS NULL OR version = ?)",
                (identity, version, version),
            ).rowcount
            if count:
                delete_events(db, identity, version)
            return count

    def prune(self, *, before: datetime, keep_serving: bool = False, dry_run: bool = False) -> int:
        """Delete versions last successful before ``before`` and return how many.

        ``keep_serving`` spares active and degraded versions; ``dry_run`` only counts.
        The highest version of each key is always kept, so a version number is never
        issued twice for different recipes.
        """
        cutoff = utc_timestamp(before)
        where = (
            "last_success_at < ? AND version < "
            "(SELECT MAX(newest.version) FROM recipes AS newest WHERE newest.key = recipes.key)"
        ) + (" AND state NOT IN ('active', 'degraded')" if keep_serving else "")
        with self._connection(write=not dry_run) as db:
            if dry_run:
                return int(
                    db.execute(f"SELECT COUNT(*) FROM recipes WHERE {where}", (cutoff,)).fetchone()[
                        0
                    ]
                )
            count = db.execute(f"DELETE FROM recipes WHERE {where}", (cutoff,)).rowcount
            if count:
                drop_orphan_events(db)
            return count
