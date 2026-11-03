"""Recipe row encoding, decoding and argument checks for the SQLite store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from pydantic import ValidationError

from rastro.adapters.sqlite_lifecycle import status
from rastro.domain.lifecycle import RecipeState
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.memory import (
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
    "state, validated_at FROM recipes WHERE key = ?"
)


def find(
    db: sqlite3.Connection, key: RecipeKey, identity: str, version: int
) -> StoredRecipe | None:
    """Decode one version, validating its definition, history and lifecycle position."""
    row = db.execute(f"{_ROW} AND version = ?", (identity, version)).fetchone()
    return None if row is None else _decode(key, row)


def find_serving(db: sqlite3.Connection, key: RecipeKey, identity: str) -> StoredRecipe | None:
    row = db.execute(f"{_ROW} AND state IN ('active', 'degraded')", (identity,)).fetchone()
    return None if row is None else _decode(key, row)


def _decode(key: RecipeKey, row: tuple[object, ...]) -> StoredRecipe:
    version, payload, index, created, success, count, state, validated = row
    status(version, state, validated)  # A pinned read must not bypass corruption checks.
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


def encode(
    key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
) -> tuple[Recipe, str]:
    """Validate a definition and its selection; return it with its canonical payload."""
    recipe = Recipe.model_validate(recipe.model_dump())
    StoredRecipe(
        key=key,
        recipe=recipe,
        metadata=RecipeMetadata(
            selected_index=selected_index, created_at=at, last_success_at=at, successful_runs=1
        ),
    )
    payload = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    if len(payload.encode()) > _MAX_PAYLOAD:
        raise RecipeStorageError("Recipe payload exceeds storage budget")
    return recipe, payload


def insert_candidate(
    db: sqlite3.Connection,
    identity: str,
    recipe: Recipe,
    payload: str,
    *,
    selected_index: int,
    at: datetime,
) -> None:
    """Insert a new version as an unvalidated candidate; lifecycle changes follow."""
    timestamp = utc_timestamp(at)
    db.execute(
        "INSERT INTO recipes (key, version, payload, selected_index, created_at, "
        "last_success_at, successful_runs, state) VALUES (?, ?, ?, ?, ?, ?, 1, 'candidate')",
        (identity, recipe.version, payload, selected_index, timestamp, timestamp),
    )


def check_version(version: int | None, *, required: bool = False) -> None:
    if version is None and not required:
        return
    if type(version) is not int or not 1 <= version <= 2_147_483_647:
        raise ConfigurationError("Recipe version is out of range")


def check_state(state: RecipeState) -> RecipeState:
    try:
        return RecipeState(state)
    except ValueError:
        raise ConfigurationError("Unknown recipe lifecycle state") from None


def check_lifecycle_input(reason: str, at: datetime) -> str:
    """Validate a lifecycle note and timestamp before storage is opened."""
    utc_timestamp(at)
    try:
        return check_reason(reason)
    except ValidationError:
        raise ConfigurationError(
            "Lifecycle reasons must be 1-500 non-blank printable characters"
        ) from None
