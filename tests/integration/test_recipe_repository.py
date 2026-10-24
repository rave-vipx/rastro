import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rastro import ConfigurationError, Recipe
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.domain.memory import RecipeConflictError, RecipeKey, RecipeStorageError

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def test_restart_immutable_versions_success_metadata_and_cleanup(tmp_path, source, schema, recipe):
    path = tmp_path / "recipes.db"
    key = RecipeKey.for_source(source, schema)
    repo = SQLiteRecipeRepository(path)
    assert not path.exists()  # Initialization is lazy, including for async callers.
    assert repo.get(key) is None
    first = repo.save_success(key, recipe, selected_index=0, at=NOW)
    assert first.successful_runs == 1
    assert SQLiteRecipeRepository(path).get(key) == first
    v2 = recipe.model_copy(update={"version": 2})
    repo.save_success(key, v2, selected_index=0, at=NOW + timedelta(days=1))
    repo.save_success(key, recipe, selected_index=0, at=NOW - timedelta(days=1))
    assert repo.get(key).recipe.version == 2
    old = repo.get(key, version=1)
    assert old.successful_runs == 2
    assert old.created_at == NOW - timedelta(days=1)
    assert old.last_success_at == NOW
    with pytest.raises(RecipeConflictError):
        repo.save_success(
            key, Recipe(strategy="jsonld", fields={"name": "/different"}), selected_index=0, at=NOW
        )
    assert repo.get(key, version=1) == old
    assert repo.prune(before=NOW) == 0  # Strict cutoff.
    assert repo.prune(before=NOW + timedelta(seconds=1)) == 1
    assert repo.get(key, version=1) is None
    assert repo.delete(key, version=1) == 0
    assert repo.delete(key) == 1
    assert repo.get(key) is None
    path.unlink()  # No connection remains open (also checked on Windows).


def test_concurrent_first_use_is_atomic(tmp_path, source, schema, recipe):
    path = tmp_path / "concurrent.db"
    key = RecipeKey.for_source(source, schema)

    def save(_):
        return SQLiteRecipeRepository(path).save_success(key, recipe, selected_index=0, at=NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(save, range(24)))
    assert sorted(item.successful_runs for item in results) == list(range(1, 25))
    assert SQLiteRecipeRepository(path).get(key).successful_runs == 24


@pytest.mark.parametrize("contents", ["foreign", "future", "corrupt", "missing_table", "payload"])
def test_unknown_or_corrupt_databases_fail_without_reset(
    tmp_path, source, schema, recipe, contents
):
    path = tmp_path / "db"
    key = RecipeKey.for_source(source, schema)
    repo = SQLiteRecipeRepository(path)
    if contents == "corrupt":
        path.write_text("private invalid database")
    else:
        if contents != "foreign":
            repo.save_success(key, recipe, selected_index=0, at=NOW)
        with closing(sqlite3.connect(path)) as db:
            if contents == "foreign":
                db.execute("CREATE TABLE private_data(value TEXT)")
            elif contents == "future":
                db.execute("PRAGMA user_version = 999")
            elif contents == "missing_table":
                db.execute("DROP TABLE recipes")
            else:
                db.execute("UPDATE recipes SET payload = ?", ('{"secret": "private"}',))
            db.commit()
    before = path.read_bytes()
    with pytest.raises(RecipeStorageError) as error:
        repo.get(key)
    assert "private" not in str(error.value) and str(path) not in str(error.value)
    assert path.read_bytes() == before


def test_lock_timeout_is_explicit_and_connections_close(tmp_path, source, schema, recipe):
    path = tmp_path / "locked.db"
    key = RecipeKey.for_source(source, schema)
    repo = SQLiteRecipeRepository(path, timeout=0.01)
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    with closing(sqlite3.connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(RecipeStorageError):
            repo.save_success(key, recipe, selected_index=0, at=NOW)
        db.rollback()
    assert repo.get(key).successful_runs == 1
    path.unlink()


def test_no_urls_records_or_schema_defaults_persisted(tmp_path, source, schema, recipe):
    path = tmp_path / "private.db"
    repo = SQLiteRecipeRepository(path)
    key = RecipeKey.for_source(source, schema)
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    data = path.read_bytes()
    for forbidden in [b"private", b"secret", b"example.com", b"https://", b"$defs"]:
        assert forbidden not in data
    assert repo.delete(key) == 1
    assert recipe.model_dump_json().encode() not in path.read_bytes()


def test_configuration_and_timestamp_boundaries(tmp_path, source, schema, recipe):
    for path in ["", ":memory:", "file:memory?mode=memory"]:
        with pytest.raises(ConfigurationError):
            SQLiteRecipeRepository(path)
    for timeout in [0, -1, 31, float("nan"), float("inf")]:
        with pytest.raises(ConfigurationError):
            SQLiteRecipeRepository(tmp_path / "x", timeout=timeout)
    repo = SQLiteRecipeRepository(tmp_path / "x")
    key = RecipeKey.for_source(source, schema)
    for version in [0, -1, 2**63, True]:
        with pytest.raises(ConfigurationError):
            repo.get(key, version=version)
        with pytest.raises(ConfigurationError):
            repo.delete(key, version=version)
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.prune(before=NOW.replace(tzinfo=None))
    assert not (tmp_path / "x").exists()
