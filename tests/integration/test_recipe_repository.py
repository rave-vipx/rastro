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
    marker = "DELETE_THIS_PRIVATE_SELECTOR"
    recipe = Recipe(strategy="jsonld", fields={"name": "/" + marker})
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    data = path.read_bytes()
    assert marker.encode() in data
    for forbidden in [b"private", b"secret", b"example.com", b"https://", b"$defs"]:
        assert forbidden not in data
    assert repo.delete(key) == 1
    assert marker.encode() not in path.read_bytes()


def test_configuration_and_timestamp_boundaries(tmp_path, source, schema, recipe):
    for path in ["", ":memory:", "file:memory?mode=memory"]:
        with pytest.raises(ConfigurationError):
            SQLiteRecipeRepository(path)
    for timeout in [0, -1, 31, float("nan"), float("inf")]:
        with pytest.raises(ConfigurationError):
            SQLiteRecipeRepository(tmp_path / "x", timeout=timeout)
    repo = SQLiteRecipeRepository(tmp_path / "x")
    key = RecipeKey.for_source(source, schema)
    for version in [0, -1, 2**63, True, 1.5, "1"]:
        with pytest.raises(ConfigurationError):
            repo.get(key, version=version)
        with pytest.raises(ConfigurationError):
            repo.delete(key, version=version)
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.prune(before=NOW.replace(tzinfo=None))
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize(
    "column,value",
    [
        ("version", 7),
        ("selected_index", 3),
        ("last_success_at", "2025-01-01T00:00:00+00:00"),
        ("payload", '{"strategy":"python","code":"PRIVATE"}'),
    ],
)
def test_corrupt_metadata_cannot_be_reused(tmp_path, source, schema, recipe, column, value):
    path = tmp_path / "db"
    repo = SQLiteRecipeRepository(path)
    key = RecipeKey.for_source(source, schema)
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    with closing(sqlite3.connect(path)) as db:
        # The column comes only from this fixed test parameter list.
        db.execute(f"UPDATE recipes SET {column} = ?", (value,))
        db.commit()
    with pytest.raises(RecipeStorageError):
        repo.get(key)


def test_oversized_serialization_and_unavailable_path_fail_explicitly(tmp_path, source, schema):
    key = RecipeKey.for_source(source, schema)
    huge = Recipe(strategy="jsonld", fields={str(i): "/" + "\U0001f600" * 1023 for i in range(100)})
    with pytest.raises(RecipeStorageError, match="budget"):
        SQLiteRecipeRepository(tmp_path / "size").save_success(key, huge, selected_index=0, at=NOW)
    assert not (tmp_path / "size").exists()
    with pytest.raises(RecipeStorageError):
        SQLiteRecipeRepository(tmp_path / "missing-parent" / "db").get(key)


def test_concurrent_conflicting_writes_preserve_one_definition(tmp_path, source, schema, recipe):
    repo = SQLiteRecipeRepository(tmp_path / "db")
    key = RecipeKey.for_source(source, schema)
    changed = Recipe(strategy="jsonld", fields={"name": "/different"})

    def save(candidate):
        try:
            return repo.save_success(key, candidate, selected_index=0, at=NOW)
        except RecipeConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (recipe, changed)))
    winner = next(item for item in results if item is not None)
    assert sum(item is None for item in results) == 1
    assert repo.get(key) == winner and winner.successful_runs == 1


def test_delete_is_scoped_and_sql_values_are_parameters(tmp_path, source, schema, recipe):
    repo = SQLiteRecipeRepository(tmp_path / "db")
    normal = RecipeKey.for_source(source, schema)
    hostile = normal.model_copy(update={"locale": "x'; DROP TABLE recipes;--"})
    for key in (normal, hostile):
        repo.save_success(key, recipe, selected_index=0, at=NOW)
    assert repo.delete(hostile, version=1) == 1
    assert repo.get(normal).recipe == recipe


def test_migration_records_application_and_schema_version(tmp_path, source, schema):
    path = tmp_path / "db"
    assert SQLiteRecipeRepository(path).get(RecipeKey.for_source(source, schema)) is None
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("PRAGMA application_id").fetchone()[0] == 0x52535452


def test_m3_payload_without_new_evidence_default_remains_reusable(
    tmp_path, source, schema, recipe, html
):
    import json

    from rastro import Outcome, Rastro

    path = tmp_path / "m3.db"
    repo = SQLiteRecipeRepository(path)
    key = RecipeKey.for_source(source, schema)
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    old_payload = recipe.model_dump(mode="json")
    old_payload.pop("require_evidence")
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE recipes SET payload = ?", (json.dumps(old_payload),))
        db.commit()
    result = Rastro(repository=repo).extract_html(html, source=source, schema=schema)
    assert result.status == Outcome.SUCCESS and result.recipe_reused
    assert repo.get(key).successful_runs == 2
    with pytest.raises(RecipeConflictError):
        repo.save_success(
            key, recipe.model_copy(update={"require_evidence": True}), selected_index=0, at=NOW
        )
