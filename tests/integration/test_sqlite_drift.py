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
        assert db.execute("SELECT version FROM drift_schema").fetchone()[0] == 2


def test_failed_runs_do_not_replace_last_success_and_conflicts_are_refused(tmp_path):
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


def test_clock_going_backwards_is_clamped_not_refused(tmp_path):
    """DRIFT-001, QUAL-005: an earlier observation time is clamped to the last recorded one."""
    monitor = SQLiteDriftMonitor(tmp_path / "data.sqlite")
    first = snapshot()
    monitor.assess("events", first)
    earlier = snapshot(run_id="earlier", observed_at=first.observed_at - timedelta(hours=1))
    assert monitor.assess("events", earlier).risk == "low"
    later = snapshot(run_id="later", observed_at=first.observed_at - timedelta(minutes=30))
    assert monitor.assess("events", later).risk == "low"
    with closing(sqlite3.connect(tmp_path / "data.sqlite")) as db:
        observed = {row[0] for row in db.execute("SELECT observed FROM drift_runs")}
    assert len(observed) == 1


def drifted(run_id="drifted", minutes=1, count=3):
    return snapshot(
        run_id=run_id,
        observed_at=datetime(2026, 11, 7, tzinfo=UTC) + timedelta(minutes=minutes),
        fields={
            "title": FieldStructure(
                selector_match_count=count, field_coverage=1, validation_failure_rate=0
            )
        },
    )


def test_elevated_drift_stays_open_and_pins_the_baseline(tmp_path):
    """DRIFT-003/005: a successful drifted run never becomes the baseline until acknowledged."""
    from rastro.adapters.sqlite_dataset_admin import acknowledge_drift

    path = tmp_path / "data.sqlite"
    monitor = SQLiteDriftMonitor(path)
    first = snapshot()
    monitor.assess("events", first)
    assert monitor.open_drift("events") == ()
    assert monitor.assess("events", drifted()).risk == "elevated"
    (item,) = monitor.open_drift("events")
    assert (item.source_url, item.risk, item.run_id) == (first.source_url, "elevated", "drifted")
    assert monitor.baseline("events", first) == first
    again = monitor.assess("events", drifted("again", minutes=2))
    assert again.risk == "elevated"
    assert monitor.open_drift("events")[0].run_id == "drifted"
    assert acknowledge_drift(path, "events", first.source_url) == 1
    assert monitor.open_drift("events") == ()
    assert monitor.assess("events", drifted("accepted", minutes=3)).risk == "low"
    assert acknowledge_drift(path, "events", first.source_url) == 0


def test_low_drift_and_failed_runs_open_nothing(tmp_path):
    monitor = SQLiteDriftMonitor(tmp_path / "data.sqlite")
    monitor.assess("events", snapshot())
    failed = drifted("failed").model_copy(update={"status": Outcome.PARTIAL_SUCCESS})
    monitor.assess("events", failed)
    monitor.assess("events", drifted("same", minutes=2, count=1))
    assert monitor.open_drift("events") == ()


def test_version_one_drift_schema_migrates_forward_and_reads_without_writing(tmp_path):
    """Persistence: a 0.6.0 drift schema (version 1) is read as-is and upgraded on write."""
    from rastro.adapters import sqlite_drift

    path = tmp_path / "data.sqlite"
    SQLiteDriftMonitor(path).assess("events", snapshot())
    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE drift_open")
        db.execute("DROP TABLE drift_rebase")
        db.execute("UPDATE drift_schema SET version = 1")
        db.commit()
    monitor = SQLiteDriftMonitor(path)
    assert monitor.open_drift("events") == ()
    assert monitor.baseline("events", snapshot()) == snapshot()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version FROM drift_schema").fetchone()[0] == 1
    monitor.assess("events", drifted())
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version FROM drift_schema").fetchone()[0] == 2
    assert len(monitor.open_drift("events")) == 1
    assert sqlite_drift.SCHEMA_VERSION == 2


def test_open_drift_read_on_missing_file_creates_nothing(tmp_path):
    from rastro.adapters.sqlite_dataset_admin import acknowledge_drift

    path = tmp_path / "data.sqlite"
    assert SQLiteDriftMonitor(path).open_drift("events") == ()
    assert acknowledge_drift(path, "events", "https://example.org/events") == 0
    assert not path.exists()


def test_recipe_change_rebaselines_without_opening_drift(tmp_path):
    """REPAIR-005: after promotion or rollback the next success is the new baseline."""
    from rastro.adapters.sqlite_dataset_admin import rebaseline_drift

    path = tmp_path / "data.sqlite"
    monitor = SQLiteDriftMonitor(path)
    monitor.assess("events", snapshot())
    monitor.assess("events", drifted())
    assert monitor.open_drift("events")
    rebaseline_drift(path, "events", snapshot().source_url)
    assert monitor.open_drift("events") == ()
    assert monitor.assess("events", drifted("new", minutes=2, count=9)).risk == "elevated"
    assert monitor.open_drift("events") == ()
    assert monitor.assess("events", drifted("next", minutes=3, count=9)).risk == "low"
