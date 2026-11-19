"""OBS-003: transactional forward-only migrations for ``run_metrics`` (v3 and v4)."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rastro.adapters import postgres_schema
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_admin import migrate_dataset
from rastro.adapters.sqlite_dataset_schema import APPLICATION_ID, SCHEMA_VERSION, migrate
from rastro.cli import main
from rastro.domain.dataset import DatasetNotFoundError, DatasetStorageError
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


def test_v2_migrates_to_current_idempotently_and_keeps_records_and_mode(tmp_path):
    assert SCHEMA_VERSION == 4
    path = tmp_path / "d.sqlite"
    status = to_v2(path)
    store = SQLiteDatasetStore(path)
    assert len(store.records("agenda")) == 1  # a read does not migrate
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    store.record_run_metrics("d", [row()])
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        migrate(db)
        migrate(db)
        assert db.execute("SELECT count(*) FROM run_metrics").fetchone()[0] == 1
    assert store.records("agenda")[0].fields == record().fields
    assert store.source_status("d") == (status,)
    assert store.run_metrics("d") == (row(),)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_v1_migrates_straight_to_current_and_old_files_still_read(tmp_path):
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


# The exact ``run_metrics`` table and columns the schema 3 code (0.7, before N5) wrote.
V3_TABLE = (
    "CREATE TABLE run_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "dataset TEXT NOT NULL, run_id TEXT NOT NULL, source_url TEXT NOT NULL, host TEXT NOT NULL, "
    "strategy TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
    "outcome TEXT NOT NULL, http_status INTEGER, requests INTEGER NOT NULL, "
    "retries INTEGER NOT NULL, bytes INTEGER NOT NULL, network_seconds REAL NOT NULL, "
    "processing_seconds REAL NOT NULL, render_seconds REAL NOT NULL, model_calls INTEGER NOT NULL, "
    "model_tokens INTEGER NOT NULL, records INTEGER NOT NULL, required_fields_ok INTEGER NOT NULL, "
    "fields_validated INTEGER NOT NULL)"
)


def to_v3(path):
    """A schema 3 file with one metrics row, written with the pre-N5 columns only."""
    status = to_v2(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(V3_TABLE)
        db.execute("CREATE INDEX run_metrics_dataset ON run_metrics (dataset, id)")
        db.execute(
            "INSERT INTO run_metrics VALUES (NULL, 'd', 'old', 'https://a.example/list', "
            "'a.example', 'explicit', ?, ?, 'success', 200, 1, 0, 100, 1.0, 1.0, 0, 0, 0, 2, 2, 4)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        db.execute("PRAGMA user_version = 3")
        db.commit()
    return status


def test_v3_rows_read_as_unknown_and_migrate_to_v4_once(tmp_path):
    """N5: old rows keep ``rejected``/``cap`` unknown (None), never a made-up 0."""
    path = tmp_path / "d.sqlite"
    status = to_v3(path)
    store = SQLiteDatasetStore(path)
    [old] = store.run_metrics("d")  # a read of a schema 3 file does not migrate
    assert (old.run_id, old.records, old.rejected, old.cap, old.cap_value) == (
        "old",
        2,
        None,
        None,
        None,
    )
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    capped = row(1).model_copy(update={"rejected": 3, "cap": "spider_page_limit", "cap_value": 25})
    store.record_run_metrics("d", [capped])
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        migrate(db)
        db.commit()
    assert store.run_metrics("d") == (old, capped)
    assert store.source_status("d") == (status,)


def test_an_aborted_v3_to_v4_migration_leaves_schema_3(tmp_path):
    path = tmp_path / "d.sqlite"
    to_v3(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        db.rollback()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = {r[1] for r in db.execute("PRAGMA table_info(run_metrics)")}
        assert "rejected" not in columns
    assert SQLiteDatasetStore(path).run_metrics("d")[0].rejected is None


def test_migrate_v3_to_v4_preserves_records(tmp_path, capsys):
    path = tmp_path / "d.sqlite"
    status = to_v3(path)
    assert main(["dataset", "migrate", str(path), "--to", "4"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert (first["from_version"], first["to_version"], first["migrated"]) == (3, 4, True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    store = SQLiteDatasetStore(path)
    assert store.records("agenda")[0].fields == record().fields
    assert store.source_status("d") == (status,)
    [old] = store.run_metrics("d")
    assert (old.run_id, old.records, old.rejected) == ("old", 2, None)
    assert main(["dataset", "migrate", str(path), "--to", "4"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["from_version"], second["to_version"], second["migrated"]) == (4, 4, False)
    v09_path = tmp_path / "v09.sqlite"
    v09_store = SQLiteDatasetStore(v09_path)
    obs = record(source="https://a.example/", at=NOW)
    v09_store.apply_snapshot("agenda", "https://a.example/", [obs], complete=True, observed_at=NOW)
    assert main(["dataset", "migrate", str(v09_path)]) == 0
    v09_out = json.loads(capsys.readouterr().out)
    assert (v09_out["from_version"], v09_out["to_version"], v09_out["migrated"]) == (4, 4, False)
    assert v09_store.records("agenda")[0].fields == obs.fields
    step_path = tmp_path / "step.sqlite"
    v1(step_path)
    for before, target in ((1, 2), (2, 3), (3, 4)):
        assert main(["dataset", "migrate", str(step_path), "--to", str(target)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert (out["from_version"], out["to_version"], out["migrated"]) == (before, target, True)
    assert SQLiteDatasetStore(step_path).records("agenda")[0].fields == record().fields


def test_migrate_refuses_unknown_version(tmp_path):
    path = tmp_path / "d.sqlite"
    to_v3(path)
    before = path.read_bytes()
    for target in ("0", "5", "99", "-1"):
        assert main(["dataset", "migrate", str(path), "--to", target]) == 2
        assert path.read_bytes() == before
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        for bad in (0, 5, 99, -1, True):
            with pytest.raises(DatasetStorageError):
                migrate(db, to=bad)  # type: ignore[arg-type]
    with pytest.raises(DatasetStorageError):
        postgres_schema.migrate(object(), "rastro", write=True, to=99)  # type: ignore[arg-type]
    with pytest.raises(DatasetNotFoundError):
        migrate_dataset(tmp_path / "missing.sqlite")
    assert main(["dataset", "migrate", str(path), "--to", "4"]) == 0
    v4_bytes = path.read_bytes()
    assert main(["dataset", "migrate", str(path), "--to", "3"]) == 2
    assert path.read_bytes() == v4_bytes
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version = 99")
    unknown_bytes = path.read_bytes()
    assert main(["dataset", "migrate", str(path)]) == 2
    assert path.read_bytes() == unknown_bytes
