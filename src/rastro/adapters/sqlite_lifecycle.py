"""Lifecycle reads and writes inside an open SQLite recipe transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime

from rastro.domain.lifecycle import LifecycleChange, RecipeState, VersionStatus
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import RecipeKey, RecipeNotFoundError, utc_timestamp

_STATUS = "SELECT version, state, validated_at FROM recipes WHERE key = ?"


def _status(row: tuple[object, ...]) -> VersionStatus:
    version, state, validated = row
    return VersionStatus.model_validate(dict(version=version, state=state, validated_at=validated))


def version_status(db: sqlite3.Connection, identity: str, version: int) -> VersionStatus:
    row = db.execute(f"{_STATUS} AND version = ?", (identity, version)).fetchone()
    if row is None:
        raise RecipeNotFoundError("No stored recipe for this context and version")
    return _status(row)


def serving_status(db: sqlite3.Connection, identity: str) -> VersionStatus | None:
    row = db.execute(f"{_STATUS} AND state IN ('active', 'degraded')", (identity,)).fetchone()
    return None if row is None else _status(row)


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
    (last,) = db.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM recipe_events WHERE key = ?", (identity,)
    ).fetchone()
    for offset, change in enumerate(changes, start=1):
        activated = change.to_state is RecipeState.ACTIVE
        db.execute(
            "UPDATE recipes SET state = ?, validated_at = CASE WHEN ? AND validated_at IS NULL "
            "THEN ? ELSE validated_at END WHERE key = ? AND version = ?",
            (change.to_state.value, activated, timestamp, identity, change.version),
        )
        db.execute(
            "INSERT INTO recipe_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                last + offset,
                change.version,
                None if change.from_state is None else change.from_state.value,
                change.to_state.value,
                change.action.value,
                reason,
                timestamp,
            ),
        )


def read_history(db: sqlite3.Connection, key: RecipeKey) -> LifecycleHistory:
    identity = key.canonical()
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
            versions=[_status(row) for row in versions],
            events=[dict(zip(fields, row, strict=True)) for row in events],
        )
    )


def drop_orphan_events(db: sqlite3.Connection) -> None:
    """Remove the history of deleted or pruned versions with their definitions."""
    db.execute(
        "DELETE FROM recipe_events WHERE NOT EXISTS (SELECT 1 FROM recipes AS stored "
        "WHERE stored.key = recipe_events.key AND stored.version = recipe_events.version)"
    )
