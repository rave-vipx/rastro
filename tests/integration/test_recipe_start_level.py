"""Recipe memory remembers the acquisition level that last worked, from schema 2 files on."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from rastro.adapters import sqlite_schema
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.domain.memory import RecipeKey, RecipeStorageError

CREATED = "2026-01-01T00:00:00.000000+00:00"


@pytest.fixture
def key(source, schema) -> RecipeKey:
    return RecipeKey.for_source(source, schema)


@pytest.fixture
def schema_2_file(tmp_path, key, legacy_payload, schema_v1_database):
    """A file exactly as the 0.9.0 release leaves it: schema 2 with one active version."""
    path = schema_v1_database(
        tmp_path / "v0.9.db", [(key.canonical(), 1, legacy_payload, 0, CREATED, 2)]
    )
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN")
        sqlite_schema._MIGRATIONS[1](db)
        db.execute("PRAGMA user_version = 2")
        db.execute("COMMIT")
    return path


def user_version(path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def test_a_schema_2_file_migrates_without_a_start_level(schema_2_file, key) -> None:
    stored = SQLiteRecipeRepository(schema_2_file).get(key)
    assert stored is not None and stored.metadata.successful_runs == 2
    assert (stored.metadata.start_level, stored.metadata.level_runs) == (None, 0)
    assert user_version(schema_2_file) == sqlite_schema.SCHEMA_VERSION == 3


def test_start_level_round_trips_and_survives_a_second_open(schema_2_file, key) -> None:
    repo = SQLiteRecipeRepository(schema_2_file)
    repo.save_start_level(key, 1, "stealth", runs=4)
    again = SQLiteRecipeRepository(schema_2_file).get(key)  # migrating twice is a no-op
    assert (again.metadata.start_level, again.metadata.level_runs) == ("stealth", 4)


def test_saving_a_start_level_never_changes_success_history(schema_2_file, key, recipe) -> None:
    repo = SQLiteRecipeRepository(schema_2_file)
    repo.save_start_level(key, 1, "browser", runs=0)
    after = repo.save_success(key, repo.get(key).recipe, selected_index=0, at=datetime.now(UTC))
    assert after.metadata.successful_runs == 3
    assert after.metadata.start_level == "browser"


def test_an_unknown_level_or_missing_version_is_refused(schema_2_file, key) -> None:
    repo = SQLiteRecipeRepository(schema_2_file)
    with pytest.raises(RecipeStorageError):
        repo.save_start_level(key, 1, "teleport", runs=0)  # type: ignore[arg-type]
    with pytest.raises(RecipeStorageError):
        repo.save_start_level(key, 9, "http", runs=0)
    with pytest.raises(RecipeStorageError):
        repo.save_start_level(key, 1, "http", runs=-1)


def test_a_failed_migration_leaves_the_schema_2_file_untouched(schema_2_file, key) -> None:
    with closing(sqlite3.connect(schema_2_file)) as db:
        db.execute("ALTER TABLE recipes ADD COLUMN start_level TEXT")  # a conflicting column
        db.commit()
    before = schema_2_file.read_bytes()
    with pytest.raises(RecipeStorageError):
        SQLiteRecipeRepository(schema_2_file).get(key)
    assert schema_2_file.read_bytes() == before
    assert user_version(schema_2_file) == 2


def test_read_only_access_never_migrates_a_schema_2_file(schema_2_file, key) -> None:
    before = schema_2_file.read_bytes()
    with pytest.raises(RecipeStorageError, match="upgrade"):
        SQLiteRecipeRepository(schema_2_file, read_only=True).get(key)
    assert schema_2_file.read_bytes() == before
