"""PR 56 regressions: DATA-001/004/005 and SYNC-002/003/005/006."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from io import StringIO
from threading import Barrier

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError, ObservedRecord
from rastro.domain.results import Evidence
from tests.integration.test_sqlite_dataset import AT, OTHER, URL, apply


def observation(fields, identity=("title",), *, source=URL, at=AT):
    return ObservedRecord(
        key=DatasetRecordKey.from_fields("agenda", fields, identity),
        source_url=source,
        observed_at=at,
        fields=fields,
        provenance={
            name: Evidence(
                source_url=source,
                observed_at=at,
                method="css",
                locator=f".{name}",
                recipe_version=1,
            )
            for name in fields
        },
    )


def test_m1_nbsp_and_internal_whitespace_share_one_record(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    a = {"title": "Gran\u00a0Show", "venue": "Sala  Uno", "start_time": "2026-11-07T20:00:00+00:00"}
    b = {"title": "Gran Show", "venue": "Sala Uno", "start_time": "2026-11-07T20:00:00+00:00"}
    identity = ("title", "venue", "start_time")
    apply(store, observation(a, identity))
    apply(store, observation(b, identity, source=OTHER), source=OTHER)
    rows = store.records("agenda")
    assert len(rows) == 1
    assert set(rows[0].origins) == {URL, OTHER}


@pytest.mark.parametrize("method", ["records", "source_status", "export_jsonl"])
def test_m2_missing_dataset_reads_fail_without_creating_a_file(tmp_path, method):
    path = tmp_path / "typo.sqlite"
    store = SQLiteDatasetStore(path)
    stream = StringIO()
    with pytest.raises(DatasetStorageError):
        getattr(store, method)("agenda", *([stream] if method == "export_jsonl" else []))
    assert not path.exists()
    assert stream.getvalue() == ""


def test_m3_partial_run_preserves_missing_fields_and_original_provenance(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    original = observation({"title": "s", "price": "10"})
    apply(store, original)
    later = AT + timedelta(hours=1)
    report = apply(store, observation({"title": "s"}, at=later), complete=False, at=later)
    row = store.records("agenda")[0]
    assert report.counts["unchanged"] == 1
    assert row.fields == row.origins[URL].fields == original.fields
    assert (
        row.provenance["price"]
        == row.origins[URL].provenance["price"]
        == original.provenance["price"]
    )
    assert row.provenance["title"].observed_at == later
    assert row.first_observed_at == AT and row.last_observed_at == later
    assert store.source_status("agenda")[0].stale
    stream = StringIO()
    store.export_jsonl("agenda", stream)
    assert '"price":"10"' in stream.getvalue()


@pytest.mark.parametrize("method", ["records", "source_status", "export_jsonl"])
def test_l1_readers_see_committed_snapshot_while_writer_holds_reserved_lock(tmp_path, method):
    path = tmp_path / "d.sqlite"
    store = SQLiteDatasetStore(path)
    apply(store, observation({"title": "s"}))
    with closing(sqlite3.connect(path, isolation_level=None)) as other:
        other.execute("BEGIN IMMEDIATE")
        other.execute("DELETE FROM dataset_records")
        stream = StringIO()
        try:
            result = getattr(store, method)(
                "agenda", *([stream] if method == "export_jsonl" else [])
            )
            assert stream.getvalue() if method == "export_jsonl" else len(result) == 1
        finally:
            other.rollback()


def test_l2_first_oversized_stored_payload_leaves_no_file_or_artifacts(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    item = observation({"title": "t", "body": "x" * 1_100_000})
    with pytest.raises(DatasetStorageError):
        apply(store, item)
    assert list(tmp_path.iterdir()) == []


def test_l2_interrupted_first_write_leaves_no_file_or_artifacts(tmp_path, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(SQLiteDatasetStore, "_link_matches", interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply(SQLiteDatasetStore(tmp_path / "d.sqlite"), observation({"title": "s"}))
    assert list(tmp_path.iterdir()) == []


def test_l3_custom_match_fields_are_used(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "c.sqlite", near_match_fields=("name", "place", "begins"))
    identity = ("name", "place", "begins")
    a = {"name": "X", "place": "P", "begins": "2026-11-07T20:00:00+00:00"}
    b = {"name": "x", "place": "p", "begins": "2026-11-07T20:05:00+00:00"}
    apply(store, observation(a, identity))
    apply(store, observation(b, identity, source=OTHER), source=OTHER)
    rows = store.records("agenda")
    assert len(rows) == 2
    assert all(len(row.uncertain_match) == 1 for row in rows)
    assert rows[0].uncertain_match == rows[1].uncertain_match


def test_l3_symlinked_parent_is_refused_without_writing(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "ln"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(DatasetStorageError):
        apply(SQLiteDatasetStore(link / "x.sqlite"), observation({"title": "s"}))
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("complete,absent", [(True, "missing"), (False, "uncertain")])
def test_l4_mixed_snapshot_classifies_every_key_once(tmp_path, complete, absent):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    before = [
        observation({"title": title, "price": "10"}) for title in ("same", "change", "absent")
    ]
    apply(store, *before)
    report = apply(
        store,
        before[0],
        observation({"title": "change", "price": "20"}),
        observation({"title": "new"}),
        complete=complete,
    )
    assert {kind: count for kind, count in report.counts.items() if count} == {
        "created": 1,
        "updated": 1,
        "unchanged": 1,
        absent: 1,
    }
    assert sum(map(len, report.keys.values())) == 4
    assert len(store.records("agenda")) == 4


def test_concurrent_first_publication_retries_without_losing_a_snapshot(tmp_path, monkeypatch):
    import os

    barrier = Barrier(2)
    original = os.link

    def publish(source, target):
        barrier.wait(timeout=5)
        original(source, target)

    monkeypatch.setattr(os, "link", publish)
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: apply(store, observation({"title": "s"})), range(2)))
    assert sum(report.counts["created"] for report in reports) == 1
    assert sum(report.counts["unchanged"] for report in reports) == 1
    assert len(store.records("agenda")) == 1
    assert [path.name for path in tmp_path.iterdir()] == ["d.sqlite"]


def test_first_publication_failure_cleans_staging(tmp_path, monkeypatch):
    import os

    def unavailable(*args):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(os, "link", unavailable)
    with pytest.raises(DatasetStorageError):
        apply(SQLiteDatasetStore(tmp_path / "d.sqlite"), observation({"title": "s"}))
    assert list(tmp_path.iterdir()) == []


def test_complete_snapshot_replaces_field_set_after_partial_preserves_it(tmp_path):
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    apply(store, observation({"title": "s", "price": "10"}))
    partial = observation({"title": "s"}, at=AT + timedelta(hours=1))
    apply(store, partial, complete=False, at=partial.observed_at)
    complete = observation({"title": "s"}, at=AT + timedelta(hours=2))
    assert apply(store, complete, at=complete.observed_at).counts["updated"] == 1
    assert store.records("agenda")[0].fields == {"title": "s"}
