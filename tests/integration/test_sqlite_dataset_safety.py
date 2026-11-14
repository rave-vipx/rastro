"""DATA-001/005, SYNC-005/006: bounded, atomic writes and safe schema ownership."""

import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_schema import APPLICATION_ID, migrate
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.sources import Limits
from tests.integration.test_sqlite_dataset import AT, OTHER, URL, apply, record


@pytest.mark.parametrize("path", ["", ":memory:", "file:test", "../db", "bad\nname", "~/.db"])
def test_bad_paths_are_typed_errors(path):
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path)


def test_symlink_is_refused_without_modifying_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("private")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(link).records("agenda")
    assert target.read_text() == "private"


@pytest.mark.parametrize(
    "mutation",
    ["missing_evidence", "wrong_source", "future_evidence", "duplicate", "time", "large", "nan"],
)
def test_invalid_snapshot_creates_no_database(tmp_path, mutation):
    path = tmp_path / "data.sqlite"
    store = SQLiteDatasetStore(path)
    item = record()
    if mutation == "missing_evidence":
        item.provenance.clear()
    elif mutation == "wrong_source":
        item = item.model_copy(update={"source_url": OTHER})
    elif mutation == "future_evidence":
        item.provenance["title"] = item.provenance["title"].model_copy(
            update={"observed_at": AT + timedelta(days=1)}
        )
    elif mutation == "time":
        item = item.model_copy(update={"observed_at": AT + timedelta(days=1)})
    elif mutation == "large":
        item.fields["title"] = "x" * 2_000_001
    elif mutation == "nan":
        item.fields["title"] = float("nan")
    with pytest.raises(DatasetStorageError):
        apply(store, item, *([item] if mutation == "duplicate" else []))
    assert not path.exists()


def test_late_run_is_clamped_and_never_regresses_observation_times(tmp_path):
    """DATA-004: a backwards clock is clamped to the last attempt, never stored as older."""
    store = SQLiteDatasetStore(tmp_path / "data.sqlite")
    later = AT + timedelta(days=1)
    apply(store, record(at=later), at=later)
    apply(store, record(start="2026-11-08T20:00:00+00:00"))
    stored = store.records("agenda")[0]
    assert stored.last_observed_at == later
    assert stored.origins[URL].observed_at == later
    assert stored.fields["start_time"] == "2026-11-08T20:00:00+00:00"
    store.mark_source_unavailable("agenda", URL, AT, "failure")
    status = store.source_status("agenda")[0]
    assert (status.last_attempt, status.stale) == (later, True)


@pytest.mark.parametrize("version,app", [(99, APPLICATION_ID), (0, 0), (1, 123)])
def test_foreign_and_future_schema_is_unchanged(tmp_path, version, app):
    path = tmp_path / "foreign.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE untouched(value TEXT)")
        db.execute(f"PRAGMA user_version = {version}")
        db.execute(f"PRAGMA application_id = {app}")
    before = path.read_bytes()
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).records("agenda")
    assert path.read_bytes() == before


def test_migration_failure_rolls_back_and_retry_is_idempotent():
    class BrokenConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if "CREATE TABLE dataset_sources" in sql:
                raise sqlite3.OperationalError("simulated")
            return super().execute(sql, *args)

    with closing(sqlite3.connect(":memory:", factory=BrokenConnection)) as db:
        with pytest.raises(sqlite3.OperationalError), db:
            db.execute("BEGIN IMMEDIATE")
            migrate(db)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_capacity_failure_rolls_back_records_and_status(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite", limits=Limits(max_records=1))
    apply(store, record(timed=True))
    with pytest.raises(DatasetStorageError):
        apply(store, record(timed=True, start="2026-11-08T20:00:00+00:00"))
    assert len(store.records("agenda")) == 1
    assert not store.source_status("agenda")[0].stale


def test_per_source_times_survive_restart_and_failed_observation(tmp_path):
    path = tmp_path / "data.sqlite"
    store = SQLiteDatasetStore(path)
    apply(store, record())
    later = AT + timedelta(days=1)
    apply(store, record(at=later), at=later)
    apply(store, record(source=OTHER), source=OTHER)
    store.mark_source_unavailable("agenda", URL, later, "failure")
    row = SQLiteDatasetStore(path).records("agenda")[0]
    assert row.origins[URL].first_observed_at == AT
    assert row.origins[URL].last_observed_at == later
    assert row.origins[OTHER].last_observed_at == AT
    assert row.observed_at == later


def test_partial_valid_records_update_without_marking_absence_missing(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite")
    apply(store, record(timed=True))
    report = apply(store, record(timed=True, start="2026-11-08T20:00:00+00:00"), complete=False)
    assert report.counts["created"] == report.counts["uncertain"] == 1
    assert report.counts["missing"] == 0


@pytest.mark.parametrize("reason", ["", " \u00a0", "a\nb", "a\x1bb", "a\u202eb"])
def test_invalid_failure_reason_does_not_create_file(tmp_path, reason):
    path = tmp_path / "data.sqlite"
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).mark_source_unavailable("agenda", URL, AT, reason)
    assert not path.exists()


def test_near_match_tolerance(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite", near_match_tolerance=timedelta(minutes=5))
    apply(store, record(timed=True))
    apply(store, record(timed=True, source=OTHER, start="2026-11-07T20:10:00+00:00"), source=OTHER)
    assert all(not row.uncertain_match for row in store.records("agenda"))


def test_repeat_source_snapshot_compares_its_own_previous_fields(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite")
    apply(store, record())
    apply(store, record(source=OTHER, start="2026-11-07T21:00:00+00:00"), source=OTHER)
    assert apply(store, record()).counts["unchanged"] == 1


def test_json_depth_and_node_limits_fail_before_file_creation(tmp_path):
    for limits in (Limits(max_json_depth=1), Limits(max_json_nodes=1)):
        path = tmp_path / "data.sqlite"
        with pytest.raises(DatasetStorageError):
            apply(SQLiteDatasetStore(path, limits=limits), record())
        assert not path.exists()


def test_near_match_markers_are_stable_on_repeated_source_updates(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite")
    a = record(timed=True)
    b = record(timed=True, source=OTHER, start="2026-11-07T20:05:00+00:00")
    apply(store, a)
    apply(store, b, source=OTHER)
    before = store.records("agenda")
    apply(store, a)
    apply(store, b, source=OTHER)
    assert store.records("agenda") == before
    assert all(len(r.uncertain_match) == 1 for r in before)


def test_healthy_complete_run_clears_stale_status(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "data.sqlite")
    store.mark_source_unavailable("agenda", URL, AT, "failure")
    assert store.source_status("agenda")[0].last_success is None
    apply(store, record())
    status = store.source_status("agenda")[0]
    assert not status.stale and status.last_success == AT and status.reason is None


def test_corrupt_database_returns_safe_typed_error(tmp_path):
    path = tmp_path / "data.sqlite"
    path.write_bytes(b"private-content")
    with pytest.raises(DatasetStorageError) as caught:
        SQLiteDatasetStore(path).records("agenda")
    assert "private-content" not in str(caught.value)
    assert str(path) not in str(caught.value)
