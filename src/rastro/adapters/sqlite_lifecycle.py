"""Lifecycle reads and writes inside an open SQLite recipe transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime

from rastro.domain.lifecycle import LifecycleAction, LifecycleChange, RecipeState, VersionStatus
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import RecipeKey, RecipeNotFoundError, utc_timestamp

_STATUS = "SELECT version, state, validated_at FROM recipes WHERE key = ?"
# Caller notes belong only to the version they describe, so deleting that version
# deletes the note. The displaced version records this fixed text instead.
SUPERSEDED_REASON = "Superseded by another version"


def status(version: object, state: object, validated_at: object) -> VersionStatus:
    """Validate one stored lifecycle position; corrupt rows raise ValidationError."""
    return VersionStatus.model_validate(
        dict(version=version, state=state, validated_at=validated_at)
    )


def version_status(db: sqlite3.Connection, identity: str, version: int) -> VersionStatus:
    row = db.execute(f"{_STATUS} AND version = ?", (identity, version)).fetchone()
    if row is None:
        raise RecipeNotFoundError("No stored recipe for this context and version")
    return status(*row)


def serving_status(db: sqlite3.Connection, identity: str) -> VersionStatus | None:
    row = db.execute(f"{_STATUS} AND state IN ('active', 'degraded')", (identity,)).fetchone()
    return None if row is None else status(*row)


def next_version(db: sqlite3.Connection, identity: str) -> int:
    """One above the highest stored version of the key in any state, or 1."""
    (highest,) = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM recipes WHERE key = ?", (identity,)
    ).fetchone()
    return int(highest) + 1


def _reserve_sequences(db: sqlite3.Connection, identity: str, count: int) -> int:
    """Reserve ``count`` event numbers and return the last number already issued."""
    row = db.execute(
        "SELECT last_sequence FROM recipe_event_sequences WHERE key = ?", (identity,)
    ).fetchone()
    last = 0 if row is None else int(row[0])
    db.execute(
        "INSERT INTO recipe_event_sequences VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
        "last_sequence = excluded.last_sequence",
        (identity, last + count),
    )
    return last


def apply_changes(
    db: sqlite3.Connection,
    identity: str,
    changes: Iterable[LifecycleChange],
    *,
    reason: str,
    at: datetime,
) -> None:
    """Apply planned changes in order and append one event per change.

    New versions (``from_state`` of ``None``) must already be inserted as candidates;
    ``reason`` must already satisfy ``check_reason``.
    """
    timestamp = utc_timestamp(at)
    planned = tuple(changes)
    last = _reserve_sequences(db, identity, len(planned))
    for offset, change in enumerate(planned, start=1):
        activated = change.to_state is RecipeState.ACTIVE
        db.execute(
            "UPDATE recipes SET state = ?, validated_at = CASE WHEN ? AND validated_at IS NULL "
            "THEN ? ELSE validated_at END WHERE key = ? AND version = ?",
            (change.to_state.value, activated, timestamp, identity, change.version),
        )
        note = SUPERSEDED_REASON if change.action is LifecycleAction.SUPERSEDED else reason
        db.execute(
            "INSERT INTO recipe_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                last + offset,
                change.version,
                None if change.from_state is None else change.from_state.value,
                change.to_state.value,
                change.action.value,
                note,
                timestamp,
            ),
        )


def read_history(db: sqlite3.Connection, key: RecipeKey, identity: str) -> LifecycleHistory:
    versions = db.execute(f"{_STATUS} ORDER BY version", (identity,)).fetchall()
    events = db.execute(
        "SELECT sequence, version, from_state, to_state, action, reason, at "
        "FROM recipe_events WHERE key = ? ORDER BY sequence",
        (identity,),
    ).fetchall()
    fields = ("sequence", "version", "from_state", "to_state", "action", "reason", "at")
    return LifecycleHistory.model_validate(
        dict(
            key=key,
            versions=[status(*row) for row in versions],
            events=[dict(zip(fields, row, strict=True)) for row in events],
        )
    )


def delete_events(db: sqlite3.Connection, identity: str, version: int | None) -> None:
    """Remove the history of deleted versions of one key, using its primary-key prefix."""
    db.execute(
        "DELETE FROM recipe_events WHERE key = ? AND (? IS NULL OR version = ?)",
        (identity, version, version),
    )
    db.execute(
        "DELETE FROM recipe_event_sequences WHERE key = ? AND NOT EXISTS "
        "(SELECT 1 FROM recipes WHERE recipes.key = ?)",
        (identity, identity),
    )


def drop_orphan_events(db: sqlite3.Connection) -> None:
    """After pruning, remove history and counters of versions that no longer exist."""
    db.execute(
        "DELETE FROM recipe_events WHERE NOT EXISTS (SELECT 1 FROM recipes AS stored "
        "WHERE stored.key = recipe_events.key AND stored.version = recipe_events.version)"
    )
    db.execute(
        "DELETE FROM recipe_event_sequences WHERE NOT EXISTS (SELECT 1 FROM recipes "
        "WHERE recipes.key = recipe_event_sequences.key)"
    )
