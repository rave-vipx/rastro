"""Write the acquisition level a stored recipe version last worked at."""

from __future__ import annotations

import sqlite3
from typing import get_args

from rastro.domain.memory import RecipeStorageError
from rastro.domain.source_notes import LadderLevel

LEVELS = frozenset(get_args(LadderLevel.__value__))


def store_start_level(
    db: sqlite3.Connection, identity: str, version: int, level: LadderLevel, runs: int
) -> None:
    """Set ``level`` and ``runs`` on one existing version; nothing else changes.

    Raises ``RecipeStorageError`` for an unknown level, negative runs or a missing version.
    """
    if level not in LEVELS or type(runs) is not int or runs < 0:
        raise RecipeStorageError("Invalid acquisition start level")
    updated = db.execute(
        "UPDATE recipes SET start_level = ?, level_runs = ? WHERE key = ? AND version = ?",
        (level, min(runs, 1_000_000), identity, version),
    ).rowcount
    if updated != 1:
        raise RecipeStorageError("Recipe version not found")
