"""Independent, transactional drift migration and history (DRIFT-001, ADR 0018)."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.drift import FieldStructure, StructuralSnapshot
from rastro.domain.models import Outcome
from rastro.ports.drift import DriftMonitor


def snapshot(**changes):
    data = dict(
        source_url="https://example.org/events",
        run_id="run-1",
        observed_at=datetime(2026, 11, 7, tzinfo=UTC),
        schema_fingerprint="a" * 64,
        status=Outcome.SUCCESS,
        fields={
            "title": FieldStructure(
                selector_match_count=1, field_coverage=1, validation_failure_rate=0
            )
        },
    )
    data.update(changes)
    return StructuralSnapshot(**data)


def test_existing_dataset_coexists_with_drift_and_repeated_runs_are_idempotent(tmp_path):
    path = tmp_path / "data.sqlite"
    store = SQLiteDatasetStore(path)
    current = snapshot()
    store.mark_source_unavailable("events", current.source_url, current.observed_at, "offline")
    before = store.source_status("events")
    monitor = SQLiteDriftMonitor(path)
    assert isinstance(monitor, DriftMonitor)
    first = monitor.assess("events", current)
    assert monitor.assess("events", current) == first
    assert monitor.baseline("events", current) == current
    assert store.source_status("events") == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM drift_runs").fetchone()[0] == 1
        assert db.execute("SELECT version FROM drift_schema").fetchone()[0] == 1


def test_failed_runs_do_not_replace_last_success_and_out_of_order_is_refused(tmp_path):
    monitor = SQLiteDriftMonitor(tmp_path / "data.sqlite")
    first = snapshot()
    monitor.assess("events", first)
    failed = snapshot(
        run_id="failed",
        observed_at=first.observed_at + timedelta(seconds=1),
        status=Outcome.INVALID_DATA,
    )
    assert monitor.assess("events", failed).verdict == "extraction_failure"
    assert monitor.baseline("events", first) == first
    assert monitor.baseline("other", first) is None
    with pytest.raises(DatasetStorageError, match="Out-of-order"):
        monitor.assess("events", snapshot(run_id="old"))
    with pytest.raises(DatasetStorageError, match="Conflicting"):
        monitor.assess("events", snapshot(status=Outcome.INVALID_DATA))


@pytest.mark.parametrize("kind", ["newer", "foreign", "symlink", "bad_payload"])
def test_drift_storage_rejects_unsafe_or_unknown_state(tmp_path, kind):
    path = tmp_path / "data.sqlite"
    monitor = SQLiteDriftMonitor(path)
    monitor.assess("events", snapshot())
    if kind == "symlink":
        link = tmp_path / "link.sqlite"
        link.symlink_to(path)
        monitor = SQLiteDriftMonitor(link)
    else:
        with closing(sqlite3.connect(path)) as db:
            if kind == "newer":
                db.execute("UPDATE drift_schema SET version = 99")
            elif kind == "foreign":
                db.execute("PRAGMA application_id = 123")
            else:
                db.execute("UPDATE drift_runs SET snapshot = '{}' ")
            db.commit()
    with pytest.raises(DatasetStorageError):
        monitor.baseline("events", snapshot())


def test_aborted_initialization_publishes_no_file(tmp_path, monkeypatch):
    from rastro.adapters import sqlite_drift

    original = sqlite_drift._migrate

    def abort(db):
        original(db)
        raise sqlite3.OperationalError("private details")

    monkeypatch.setattr(sqlite_drift, "_migrate", abort)
    path = tmp_path / "data.sqlite"
    with pytest.raises(DatasetStorageError, match=r"^Dataset operation failed$"):
        SQLiteDriftMonitor(path).assess("events", snapshot())
    assert list(tmp_path.iterdir()) == []


def test_baseline_reads_never_create_or_migrate(tmp_path):
    path = tmp_path / "data.sqlite"
    monitor = SQLiteDriftMonitor(path)
    with pytest.raises(DatasetStorageError):
        monitor.baseline("events", snapshot())
    assert not path.exists()
    SQLiteDatasetStore(path).mark_source_unavailable(
        "events", snapshot().source_url, snapshot().observed_at, "offline"
    )
    assert monitor.baseline("events", snapshot()) is None
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'drift_schema'").fetchall()


def test_aborted_migration_preserves_existing_dataset(tmp_path, monkeypatch):
    from rastro.adapters import sqlite_drift

    path = tmp_path / "data.sqlite"
    store = SQLiteDatasetStore(path)
    store.mark_source_unavailable(
        "events", snapshot().source_url, snapshot().observed_at, "offline"
    )
    before = store.source_status("events")
    original = sqlite_drift._migrate

    def abort(db):
        original(db)
        raise sqlite3.OperationalError("abort")

    monkeypatch.setattr(sqlite_drift, "_migrate", abort)
    with pytest.raises(DatasetStorageError):
        SQLiteDriftMonitor(path).assess("events", snapshot())
    assert store.source_status("events") == before
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'drift_schema'").fetchall()
