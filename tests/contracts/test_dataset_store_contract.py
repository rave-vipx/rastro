"""Identical dataset behavior across adapters."""

import io
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_admin import forget_source
from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError, ObservedRecord
from rastro.domain.results import Evidence

AT = datetime(2026, 1, 1, tzinfo=UTC)
URL = "https://example.com/a"


def observation(identity="one", source=URL, at=AT, time="2026-01-02T12:00:00Z"):
    fields = dict(title="Show", venue="Room", start_time=time)
    return ObservedRecord(
        key=DatasetRecordKey(dataset="events", identity=(identity,)),
        source_url=source,
        observed_at=at,
        fields=fields,
        provenance={
            k: Evidence(
                source_url=source, observed_at=at, method="css", locator="h1", recipe_version=1
            )
            for k in fields
        },
    )


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        yield SQLiteDatasetStore(tmp_path / "dataset.db")
        return
    dsn = os.environ.get("RASTRO_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("RASTRO_TEST_POSTGRES_DSN is not set")
    import psycopg
    from psycopg import sql

    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    schema = "test_" + uuid4().hex
    adapter = PostgresDatasetStore(dsn, schema=schema)
    try:
        yield adapter
    finally:
        adapter.close()
        with psycopg.connect(dsn) as db:
            db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def apply(store, record):
    return store.apply_snapshot(
        "events", record.source_url, [record], complete=True, observed_at=record.observed_at
    )


def test_repeat_update_distinct_and_unavailable(store):
    first = observation()
    assert apply(store, first).counts["created"] == 1
    assert apply(store, first).counts["unchanged"] == 1
    later = observation(at=AT + timedelta(days=1), time="2026-01-03T12:00:00Z")
    assert apply(store, later).counts["updated"] == 1
    assert apply(store, observation("two", at=later.observed_at)).counts["created"] == 1
    before = store.records("events")
    store.record_source_failure("events", URL, AT + timedelta(days=2), "failed", "timeout")
    assert store.records("events") == before
    assert len(before) == 2
    status = store.source_status("events")[0]
    assert status.stale and status.last_success == later.observed_at
    assert status.detail == "timeout"


def test_uncertain_and_export_identical(store, tmp_path):
    reference = SQLiteDatasetStore(tmp_path / "reference.db")
    for record in (observation(), observation("two", source="https://example.com/b")):
        apply(reference, record)
        apply(store, record)
    assert all(r.uncertain_match for r in store.records("events"))
    expected, actual = io.StringIO(), io.StringIO()
    reference.export_jsonl("events", expected)
    store.export_jsonl("events", actual)
    assert actual.getvalue().encode() == expected.getvalue().encode()


def test_forget_keeps_records(store):
    apply(store, observation())
    before = store.records("events")
    if isinstance(store, SQLiteDatasetStore):
        forget_source(store._path, "events", URL, sample_sources=set())
    else:
        assert store.forget_source("events", URL) == 1
    assert not store.source_status("events")
    assert store.records("events") == before


def test_failed_snapshot_rolls_back(store):
    apply(store, observation())
    before = store.records("events"), store.source_status("events")
    from rastro.domain.sources import Limits

    store._limits = Limits(max_records=1)
    with pytest.raises(DatasetStorageError):
        apply(store, observation("two", at=AT + timedelta(days=1)))
    assert (store.records("events"), store.source_status("events")) == before


def test_migration_idempotent_and_newer_refused(store):
    if isinstance(store, SQLiteDatasetStore):
        apply(store, observation())
        apply(store, observation())
        return
    apply(store, observation())
    store.close()
    apply(store, observation())
    with store._connection() as db:
        db.execute("UPDATE rastro_schema SET version = 2")
    with pytest.raises(DatasetStorageError):
        apply(store, observation())


def test_partial_and_clock_regression(store):
    apply(store, observation(at=AT + timedelta(days=2)))
    report = apply(store, observation(at=AT))
    assert report.counts["unchanged"] == 1
    assert store.records("events")[0].last_observed_at == AT + timedelta(days=2)
    report = store.apply_snapshot("events", URL, [], complete=False, observed_at=AT)
    assert report.counts["uncertain"] == 1
    assert store.source_status("events")[0].reason == "partial_snapshot"
    store.mark_source_unavailable("events", URL, AT, "failed")
    assert store.source_status("events")[0].last_attempt == AT + timedelta(days=2)


def test_job_load_health_forget_and_history(store, tmp_path, monkeypatch):
    import json

    from rastro.application.health import job_health
    from rastro.application.job_dataset import load_job_file
    from rastro.application.job_maintenance import forget_job_source
    from rastro.application.repair_context import RepairContext
    from rastro.domain.job_results import JobExitCode

    config = {"name": "events", "identity": ["title"]}
    if isinstance(store, SQLiteDatasetStore):
        config["path"] = store._path.name
    else:
        monkeypatch.setenv("RASTRO_PG_DSN", os.environ["RASTRO_TEST_POSTGRES_DSN"])
        config.update(backend="postgres", dsn_env="RASTRO_PG_DSN", schema=store._schema)
    page = tmp_path / "page.html"
    page.write_text("<ul><li><b>Show</b></li></ul>")
    job = {
        "format_version": 1,
        "fields": [{"name": "title", "type": "string"}],
        "sources": [{"path": "page.html"}],
        "dataset": config,
        "recipe_memory": "recipes.sqlite",
        "recipe": {"strategy": "css", "record_selector": "li", "fields": {"title": "b::text"}},
    }
    file = tmp_path / "job.json"
    file.write_text(json.dumps(job))
    assert load_job_file(file).exit_code == JobExitCode.SUCCESS
    assert job_health(file).healthy
    assert RepairContext.load(file, False).history()
    page.rename(tmp_path / "other.html")
    job["sources"] = [{"path": "other.html"}]
    file.write_text(json.dumps(job))
    removed = forget_job_source(file, "https://local.invalid/page.html")
    assert removed["removed"]["dataset_sources"] == 1
    assert store.records("events")


def test_missing_schema_read_does_not_create(store):
    from rastro.domain.dataset import DatasetNotFoundError

    with pytest.raises(DatasetNotFoundError):
        store.records("events")
    if not isinstance(store, SQLiteDatasetStore):
        import psycopg

        with psycopg.connect(os.environ["RASTRO_TEST_POSTGRES_DSN"]) as db:
            assert (
                db.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname = %s", (store._schema,)
                ).fetchone()
                is None
            )


def test_failed_initial_migration_rolls_back(store):
    if isinstance(store, SQLiteDatasetStore):
        pytest.skip("PostgreSQL-specific migration collision")
    import psycopg
    from psycopg import sql

    with psycopg.connect(os.environ["RASTRO_TEST_POSTGRES_DSN"]) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(store._schema)))
        db.execute(
            sql.SQL("CREATE TABLE {}.dataset_sources (foreign_data TEXT)").format(
                sql.Identifier(store._schema)
            )
        )
    with pytest.raises(DatasetStorageError):
        apply(store, observation())
    with psycopg.connect(os.environ["RASTRO_TEST_POSTGRES_DSN"]) as db:
        assert db.execute(
            "SELECT to_regclass(%s)", (store._schema + ".rastro_schema",)
        ).fetchone() == (None,)
        assert db.execute(
            "SELECT to_regclass(%s)", (store._schema + ".dataset_records",)
        ).fetchone() == (None,)


def test_long_identity(store):
    # The domain permits 32 identity components, each up to 1024 characters.
    item = observation()
    key = DatasetRecordKey(
        dataset="events", identity=tuple("".join(uuid4().hex for _ in range(32)) for _ in range(8))
    )
    item = item.model_copy(update={"key": key})
    assert apply(store, item).counts["created"] == 1
    assert apply(store, item).counts["unchanged"] == 1
    assert store.records("events")[0].key == key


def test_forget_missing_source_is_noop(store):
    if isinstance(store, SQLiteDatasetStore):
        assert (
            forget_source(store._path, "events", URL, sample_sources=set())["dataset_sources"] == 0
        )
    else:
        assert store.forget_source("events", URL) == 0
