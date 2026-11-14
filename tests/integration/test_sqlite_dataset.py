"""DATA-001/004/005, SYNC-002/003/005/006: durable, non-destructive snapshots."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.domain.dataset import (
    DatasetRecordKey,
    DatasetStorageError,
    ObservedRecord,
    StoredRecord,
)
from rastro.domain.results import Evidence

AT = datetime(2026, 11, 6, tzinfo=UTC)
URL = "https://venue.example/events"
OTHER = "https://tickets.example/events"


def record(*, start="2026-11-07T20:00:00+00:00", source=URL, at=AT, timed=False):
    fields = {"title": "Show", "venue": "Hall", "start_time": start}
    return ObservedRecord(
        key=DatasetRecordKey.from_fields(
            "agenda", fields, ("title", "venue", "start_time") if timed else ("title", "venue")
        ),
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


def apply(store, *items, complete=True, at=AT, source=URL):
    return store.apply_snapshot("agenda", source, items, complete=complete, observed_at=at)


@pytest.fixture
def store(tmp_path):
    return SQLiteDatasetStore(tmp_path / "dataset.sqlite")


def test_second_identical_snapshot_keeps_count_and_reports_unchanged(store):
    assert apply(store, record()).counts["created"] == 1
    report = apply(store, record(at=AT + timedelta(days=1)), at=AT + timedelta(days=1))
    assert report.counts["unchanged"] == 1
    assert len(store.records("agenda")) == 1


def test_rescheduled_time_updates_same_key_when_identity_excludes_time(store):
    apply(store, record())
    assert apply(store, record(start="2026-11-07T21:00:00+00:00")).counts["updated"] == 1
    assert len(store.records("agenda")) == 1


@pytest.mark.parametrize("complete,kind", [(True, "missing"), (False, "uncertain")])
def test_reschedule_with_time_in_identity_creates_record(store, complete, kind):
    old = record(timed=True)
    apply(store, old)
    report = apply(store, record(timed=True, start="2026-11-07T21:00:00+00:00"), complete=complete)
    assert report.counts["created"] == 1
    assert report.keys[kind] == (old.key,)
    assert len(store.records("agenda")) == 2


def test_two_performances_same_show_different_start_are_distinct(store):
    apply(store, record(timed=True), record(timed=True, start="2026-11-07T22:00:00+00:00"))
    assert len(store.records("agenda")) == 2


def test_partial_snapshot_marks_uncertain_and_deletes_nothing(store):
    apply(store, record())
    assert apply(store, complete=False).counts["uncertain"] == 1
    assert store.records("agenda")[0].last_observed_at == AT
    assert store.source_status("agenda")[0].stale


def test_source_unavailable_keeps_records_with_last_observed_at(store):
    apply(store, record())
    later = AT + timedelta(days=1)
    store.mark_source_unavailable("agenda", URL, later, "access_restriction")
    assert store.records("agenda")[0].last_observed_at == AT
    status = store.source_status("agenda")[0]
    assert (status.last_success, status.last_attempt, status.stale) == (AT, later, True)
    assert status.reason == "access_restriction"


def test_near_match_across_sources_flags_uncertain_match_with_both_origins(store):
    apply(store, record(timed=True))
    apply(store, record(timed=True, source=OTHER, start="2026-11-07T20:10:00+00:00"), source=OTHER)
    rows = store.records("agenda")
    assert len(rows) == 2
    assert rows[0].uncertain_match == rows[1].uncertain_match
    assert set(rows[0].uncertain_match[0].origins) == {URL, OTHER}


def test_cross_source_equal_identity_retains_both_origins(store):
    apply(store, record())
    apply(store, record(source=OTHER), source=OTHER)
    assert set(store.records("agenda")[0].origins) == {URL, OTHER}
    assert apply(store).counts["missing"] == 1
    assert len(store.records("agenda")) == 1


def test_export_jsonl_round_trips_fields_provenance_and_observation_times(store):
    apply(store, record())
    later = AT + timedelta(days=1)
    apply(store, record(at=later), at=later)
    stream = StringIO()
    store.export_jsonl("agenda", stream)
    restored = StoredRecord.model_validate_json(stream.getvalue())
    assert restored == store.records("agenda")[0]
    assert restored.first_observed_at == AT
    assert restored.last_observed_at == later
    assert restored.provenance == record(at=later).provenance


def test_refuses_recipe_memory_file(tmp_path):
    path = tmp_path / "recipes.sqlite"
    SQLiteRecipeRepository(path).prune(before=AT)
    before = path.read_bytes()
    with pytest.raises(DatasetStorageError):
        SQLiteDatasetStore(path).records("agenda")
    assert path.read_bytes() == before


def test_concurrent_apply_is_serialized(store):
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: apply(store, record()), range(2)))
    assert sum(r.counts["created"] for r in reports) == 1
    assert sum(r.counts["unchanged"] for r in reports) == 1
    assert len(store.records("agenda")) == 1


def test_clock_going_backwards_clamps_source_times_instead_of_failing(store):
    """DATA-004, QUAL-005: an earlier clock is clamped to the last recorded attempt."""
    apply(store, record())
    earlier = AT - timedelta(hours=1)
    assert apply(store, record(at=earlier), at=earlier).counts["unchanged"] == 1
    status = store.source_status("agenda")[0]
    assert (status.last_success, status.last_attempt, status.stale) == (AT, AT, False)
    store.mark_source_unavailable("agenda", URL, earlier, "transient_failure")
    status = store.source_status("agenda")[0]
    assert (status.last_success, status.last_attempt, status.stale) == (AT, AT, True)
