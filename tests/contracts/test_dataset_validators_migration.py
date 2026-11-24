"""Transactional forward-only v1→v2 (and onward) dataset migration."""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_schema import APPLICATION_ID, SCHEMA_VERSION, migrate
from rastro.domain.dataset import DatasetStorageError, RecordOrigin, SourceStatus, StoredRecord
from tests.integration.test_sqlite_dataset import record


def v1(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE dataset_records (dataset TEXT, key TEXT, payload TEXT NOT NULL, "
            "PRIMARY KEY(dataset, key)) WITHOUT ROWID"
        )
        db.execute(
            "CREATE TABLE dataset_sources (dataset TEXT, source TEXT, payload TEXT NOT NULL, "
            "PRIMARY KEY(dataset, source)) WITHOUT ROWID"
        )
        db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        db.execute("PRAGMA user_version = 1")
        status = SourceStatus(
            source_url="https://a.example/",
            last_success=datetime.now(UTC),
            last_attempt=datetime.now(UTC),
            stale=False,
        )
        observed = record(source=status.source_url)
        origin = RecordOrigin(
            **observed.model_dump(),
            first_observed_at=observed.observed_at,
            last_observed_at=observed.observed_at,
        )
        stored = StoredRecord(**origin.model_dump(), origins={status.source_url: origin})
        db.execute(
            "INSERT INTO dataset_records VALUES (?, ?, ?)",
            ("agenda", stored.key.model_dump_json(), stored.model_dump_json()),
        )
        db.execute(
            "INSERT INTO dataset_sources VALUES (?, ?, ?)",
            ("d", status.source_url, status.model_dump_json()),
        )
        db.commit()
    return status


def test_v1_opens_migrates_idempotently_and_preserves_status_and_mode(tmp_path):
    path = tmp_path / "d.sqlite"
    status = v1(path)
    store = SQLiteDatasetStore(path)
    assert len(store.records("agenda")) == 1
    assert store.records("agenda")[0].fields == record().fields
    assert store.source_status("d") == (status,)
    assert store.get_validators("d", status.source_url) == {}
    store.save_validators("d", status.source_url, {"etag": '"v1"'})
    assert SQLiteDatasetStore(path).get_validators("d", status.source_url) == {"etag": '"v1"'}
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        migrate(db)
        migrate(db)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_migration_rolls_back_and_newer_versions_are_refused(tmp_path):
    path = tmp_path / "d.sqlite"
    v1(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        db.rollback()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='dataset_validators'"
        ).fetchone()
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).source_status("d")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_validators_are_bound_to_the_successful_snapshot_generation(tmp_path):
    """A crash or late writer between snapshot and validators cannot reuse stale tags."""
    from datetime import timedelta

    from tests.integration.test_sqlite_dataset import AT, URL

    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    store.apply_snapshot("agenda", URL, [record()], complete=True, observed_at=AT)
    store.save_validators("agenda", URL, {"etag": '"first"'})
    later = AT + timedelta(seconds=1)
    store.apply_snapshot("agenda", URL, [record(at=later)], complete=True, observed_at=later)
    assert store.get_validators("agenda", URL) == {}
    store.save_validators("agenda", URL, {"etag": '"late-first"'}, observed_at=AT)
    assert store.get_validators("agenda", URL) == {}
    store.save_validators("agenda", URL, {"etag": '"second"'}, observed_at=later)
    store.mark_source_unchanged("agenda", URL, later + timedelta(seconds=1))
    assert store.get_validators("agenda", URL) == {"etag": '"second"'}


def _version(path):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def test_read_paths_do_not_migrate_a_v1_file(tmp_path):
    """Studio/MCP reads must not mutate the dataset file."""
    path = tmp_path / "d.sqlite"
    status = v1(path)
    before = path.read_bytes()
    store = SQLiteDatasetStore(path)
    assert len(store.records("agenda")) == 1
    assert store.source_status("d") == (status,)
    assert store.dataset_names() == ("d",)
    assert store.get_validators("d", status.source_url) == {}
    assert _version(path) == 1
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_v1_file_in_a_read_only_directory_can_be_read(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "d.sqlite"
    status = v1(path)
    path.chmod(0o400)
    folder.chmod(0o500)
    try:
        store = SQLiteDatasetStore(path)
        assert len(store.records("agenda")) == 1
        assert store.source_status("d") == (status,)
    finally:
        folder.chmod(0o700)


def test_write_path_migrates_v1_to_v2(tmp_path):
    path = tmp_path / "d.sqlite"
    status = v1(path)
    SQLiteDatasetStore(path).mark_source_unavailable(
        "d", status.source_url, datetime.now(UTC), "boom"
    )
    assert _version(path) == SCHEMA_VERSION


def test_rejected_dataset_connections_are_closed(tmp_path, monkeypatch):
    path = tmp_path / "d.sqlite"
    v1(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version = 99")
    opened = []
    real = sqlite3.connect

    def spy(*args, **kwargs):
        opened.append(real(*args, **kwargs))
        return opened[-1]

    monkeypatch.setattr(sqlite3, "connect", spy)
    for call in (
        lambda s: s.source_status("d"),
        lambda s: s.mark_source_unavailable("d", "https://a.example/", datetime.now(UTC), "x"),
    ):
        with pytest.raises(DatasetStorageError):
            call(SQLiteDatasetStore(path))
    assert opened
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
