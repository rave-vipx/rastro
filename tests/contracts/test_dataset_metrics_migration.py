"""OBS-003: transactional forward-only v2 -> v3 migration adding ``run_metrics``."""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_schema import APPLICATION_ID, SCHEMA_VERSION, migrate
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.metrics import RunMetric
from tests.contracts.test_dataset_validators_migration import v1
from tests.integration.test_sqlite_dataset import record

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


def row(n: int = 0, *, url: str = "https://a.example/list") -> RunMetric:
    return RunMetric(
        run_id=f"run{n}",
        source_url=url,
        host="a.example",
        strategy="explicit",
        started_at=NOW + timedelta(seconds=n),
        finished_at=NOW + timedelta(seconds=n + 2),
        outcome="success",
        http_status=200,
        requests=1,
        bytes=100,
        network_seconds=1.0,
        processing_seconds=1.0,
        records=2,
        required_fields_ok=2,
        fields_validated=4,
    )


def to_v2(path):
    """A real schema 2 file: v1 plus the validators table and version 2 (slice B's migrate)."""
    status = v1(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE dataset_validators (dataset TEXT, source TEXT, origin TEXT NOT NULL, "
            "observed_at TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(dataset, source)) WITHOUT ROWID"
        )
        db.execute("PRAGMA user_version = 2")
        db.commit()
    return status


def test_v2_migrates_to_v3_idempotently_and_keeps_records_and_mode(tmp_path):
    assert SCHEMA_VERSION == 3
    path = tmp_path / "d.sqlite"
    status = to_v2(path)
    store = SQLiteDatasetStore(path)
    assert len(store.records("agenda")) == 1  # a read does not migrate
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    store.record_run_metrics("d", [row()])
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        migrate(db)
        migrate(db)
        assert db.execute("SELECT count(*) FROM run_metrics").fetchone()[0] == 1
    assert store.records("agenda")[0].fields == record().fields
    assert store.source_status("d") == (status,)
    assert store.run_metrics("d") == (row(),)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_v1_migrates_straight_to_v3_and_old_files_still_read(tmp_path):
    path = tmp_path / "d.sqlite"
    v1(path)
    store = SQLiteDatasetStore(path)
    assert store.run_metrics("d") == ()  # schema 1 has no table; reading creates nothing
    store.record_run_metrics("d", [row()])
    with closing(sqlite3.connect(path)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"dataset_validators", "run_metrics"} <= names


def test_migration_rolls_back_and_newer_versions_are_refused(tmp_path):
    path = tmp_path / "d.sqlite"
    to_v2(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        db.rollback()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='run_metrics'").fetchone()
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).record_run_metrics("d", [row()])
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).run_metrics("d")
    assert APPLICATION_ID


def test_retention_keeps_the_newest_rows_per_dataset(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite", metrics_retention=5)
    for n in range(4):
        store.record_run_metrics("d", [row(n * 2), row(n * 2 + 1)])
    store.record_run_metrics("other", [row(99)])
    kept = store.run_metrics("d")
    assert [r.run_id for r in kept] == ["run3", "run4", "run5", "run6", "run7"]
    assert len(store.run_metrics("other")) == 1


def test_rows_never_store_url_queries_or_userinfo():
    clean = RunMetric.model_validate(
        {**row().model_dump(), "source_url": "https://u:p@a.example/x?token=s#f"}
    )
    assert clean.source_url == "https://a.example/x"
