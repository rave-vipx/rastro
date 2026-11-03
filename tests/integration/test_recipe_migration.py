import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.domain.lifecycle import LifecycleAction, RecipeState
from rastro.domain.memory import RecipeKey, RecipeStorageError

CREATED = ["2026-01-01T00:00:00.000000+00:00", "2026-02-01T00:00:00.000000+00:00"]
A, R = RecipeState.ACTIVE, RecipeState.RETIRED


def payload(legacy_payload: str, version: int) -> str:
    return legacy_payload.replace('"version":1', f'"version":{version}')


@pytest.fixture
def keys(source, schema) -> tuple[RecipeKey, RecipeKey]:
    first = RecipeKey.for_source(source, schema)
    return first, first.model_copy(update={"locale": "en-US"})


@pytest.fixture
def old_database(tmp_path, keys, legacy_payload, schema_v1_database):
    first, second = keys
    rows = [
        (first.canonical(), 1, payload(legacy_payload, 1), 0, CREATED[0], 3),
        (first.canonical(), 3, payload(legacy_payload, 3), 0, CREATED[1], 1),
        (first.canonical(), 2, payload(legacy_payload, 2), 0, CREATED[0], 2),
        (second.canonical(), 1, payload(legacy_payload, 1), 0, CREATED[1], 5),
    ]
    return schema_v1_database(tmp_path / "v0.1.db", rows)


def markers(path) -> tuple[int, int]:
    with closing(sqlite3.connect(path)) as db:
        return (
            db.execute("PRAGMA user_version").fetchone()[0],
            db.execute("PRAGMA application_id").fetchone()[0],
        )


def test_schema_1_rows_keep_loading_with_highest_version_active(old_database, keys, recipe):
    first, second = keys
    repo = SQLiteRecipeRepository(old_database)
    served = repo.get(first)
    assert served.recipe.version == 3 and served.metadata.state is A
    assert served.recipe.fields == recipe.fields
    assert repo.get(first, version=1).metadata.successful_runs == 3
    assert repo.get(second).metadata.state is A
    assert markers(old_database) == (2, 0x52535452)
    history = repo.history(first)
    assert {item.version: item.state for item in history.versions} == {1: R, 2: R, 3: A}
    # Every schema 1 row passed validation, so each remains a rollback target.
    assert all(item.validated_at is not None for item in history.versions)
    assert [
        (e.sequence, e.version, e.from_state, e.to_state, e.action) for e in history.events
    ] == [
        (1, 1, None, R, LifecycleAction.MIGRATED),
        (2, 2, None, R, LifecycleAction.MIGRATED),
        (3, 3, None, A, LifecycleAction.MIGRATED),
    ]
    assert history.events[0].at == datetime(2026, 1, 1, tzinfo=UTC)
    restored = repo.rollback(first, 1, reason="Return to first version", at=datetime.now(UTC))
    assert restored.metadata.state is A and repo.get(first).recipe.version == 1


def test_migrated_rows_keep_recording_successes(old_database, keys, recipe):
    first, _ = keys
    repo = SQLiteRecipeRepository(old_database)
    again = repo.save_success(first, recipe, selected_index=0, at=datetime.now(UTC))
    assert again.metadata.successful_runs == 4 and again.metadata.state is R
    assert repo.get(first).recipe.version == 3


def test_fresh_and_migrated_databases_share_one_schema(tmp_path, old_database, keys):
    fresh = tmp_path / "fresh.db"
    for path in (fresh, old_database):
        SQLiteRecipeRepository(path).get(keys[0])

    def schema(path):
        with closing(sqlite3.connect(path)) as db:
            rows = db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")
            # The schema 1 fixture indents its DDL differently; compare normalized SQL.
            return [(kind, name, " ".join((sql or "").split())) for kind, name, sql in rows]

    assert schema(fresh) == schema(old_database)


def test_failed_migration_leaves_schema_1_file_untouched(old_database, keys):
    with closing(sqlite3.connect(old_database)) as db:
        db.execute("CREATE TABLE recipe_events(value TEXT)")
        db.commit()
    before = old_database.read_bytes()
    with pytest.raises(RecipeStorageError):
        SQLiteRecipeRepository(old_database).get(keys[0])
    assert old_database.read_bytes() == before
    assert markers(old_database) == (1, 0x52535452)


@pytest.mark.parametrize(("version", "application"), [(3, 0x52535452), (1, 7), (2, 7), (2, 0)])
def test_newer_or_foreign_versioned_files_are_not_migrated(tmp_path, keys, version, application):
    path = tmp_path / "other.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE recipes(value TEXT)")
        db.execute(f"PRAGMA user_version = {version}")
        db.execute(f"PRAGMA application_id = {application}")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(RecipeStorageError):
        SQLiteRecipeRepository(path).get(keys[0])
    assert path.read_bytes() == before
