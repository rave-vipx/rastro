"""Forward-only schema migrations for the SQLite recipe store."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from rastro.domain.memory import RecipeStorageError

APPLICATION_ID = 0x52535452  # RSTR


def _create_recipes(db: sqlite3.Connection) -> None:
    """Schema 1 (M3 and 0.1.0): immutable versions with success history."""
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


def _add_lifecycle(db: sqlite3.Connection) -> None:
    """Schema 2 (v0.2): lifecycle state per version and an append-only event log.

    Schema 1 reused the highest version of each key, so that version becomes
    ``active`` and older ones ``retired``. Every schema 1 row passed validation,
    so all of them remain eligible for rollback.
    """
    db.execute(
        "ALTER TABLE recipes ADD COLUMN state TEXT NOT NULL DEFAULT 'active' "
        "CHECK (state IN ('candidate', 'active', 'degraded', 'retired'))"
    )
    db.execute("ALTER TABLE recipes ADD COLUMN validated_at TEXT")
    db.execute("UPDATE recipes SET validated_at = created_at")
    db.execute(
        "UPDATE recipes SET state = 'retired' WHERE version < "
        "(SELECT MAX(version) FROM recipes AS newer WHERE newer.key = recipes.key)"
    )
    db.execute(
        "CREATE UNIQUE INDEX recipe_serving ON recipes(key) WHERE state IN ('active', 'degraded')"
    )
    db.execute("""CREATE TABLE recipe_events (
        key TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        version INTEGER NOT NULL CHECK (version > 0),
        from_state TEXT,
        to_state TEXT NOT NULL,
        action TEXT NOT NULL,
        reason TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (key, sequence)
    ) WITHOUT ROWID""")
    db.execute(
        "INSERT INTO recipe_events SELECT key, "
        "ROW_NUMBER() OVER (PARTITION BY key ORDER BY version), version, NULL, state, "
        "'migrated', 'Imported from recipe schema 1', created_at FROM recipes"
    )
    # Sequence numbers are never reissued while a key keeps any stored version.
    db.execute("""CREATE TABLE recipe_event_sequences (
        key TEXT PRIMARY KEY,
        last_sequence INTEGER NOT NULL CHECK (last_sequence > 0)
    ) WITHOUT ROWID""")
    db.execute(
        "INSERT INTO recipe_event_sequences "
        "SELECT key, MAX(sequence) FROM recipe_events GROUP BY key"
    )


# Index i migrates schema i to schema i + 1. Never edit or reorder released steps.
_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_create_recipes, _add_lifecycle)
SCHEMA_VERSION = len(_MIGRATIONS)


def _markers(db: sqlite3.Connection) -> tuple[int, int]:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    app = db.execute("PRAGMA application_id").fetchone()[0]
    return version, app


def _check_known(db: sqlite3.Connection, version: int, app: int) -> None:
    if version == 0 and app == 0:
        if db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            raise RecipeStorageError("Unrecognized recipe database; no migration applied")
        return
    if app != APPLICATION_ID or not 0 < version < SCHEMA_VERSION:
        raise RecipeStorageError("Unrecognized recipe database; no migration applied")


def migrate(db: sqlite3.Connection) -> None:
    """Bring a new or older Rastro database to ``SCHEMA_VERSION`` in one transaction.

    Foreign, newer or partially initialized files fail without modification.
    """
    if _markers(db) == (SCHEMA_VERSION, APPLICATION_ID):
        return
    # Recheck inside the write transaction: two processes may migrate the file together.
    db.execute("BEGIN IMMEDIATE")
    try:
        version, app = _markers(db)
        if (version, app) != (SCHEMA_VERSION, APPLICATION_ID):
            _check_known(db, version, app)
            for step in _MIGRATIONS[version:]:
                step(db)
            db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        db.rollback()
        raise
    db.commit()


def check_current(db: sqlite3.Connection) -> None:
    """Accept only a file already at ``SCHEMA_VERSION``; read-only access never migrates."""
    if _markers(db) != (SCHEMA_VERSION, APPLICATION_ID):
        raise RecipeStorageError(
            "Recipe storage needs an upgrade; run a writing command such as 'rastro job load'"
        )
