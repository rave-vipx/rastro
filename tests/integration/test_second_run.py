"""Offline first-cut proof: DATA-001/003, SYNC-002/006/008, COST-001."""

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main

SAMPLE = Path("examples/uy-dance-agenda/sample")


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(SAMPLE, tmp_path / "sample")
    return tmp_path / "sample"


def load(sample, capsys):
    code = main(["job", "load", str(sample / "job.toml")])
    captured = capsys.readouterr()
    assert code in (0, 1, 3), captured.err
    return json.loads(captured.out)


def records(sample):
    return SQLiteDatasetStore(sample / "agenda.sqlite").records("uy_dance_agenda")


def update(sample):
    for name in ("sodre-cartelera.html", "tickantel-evento.html"):
        shutil.copyfile(sample / "v2" / name, sample / name)


def test_repeat_load_keeps_event_count(sample, capsys):
    first = load(sample, capsys)
    before = records(sample)
    second = load(sample, capsys)
    assert len(before) == len(records(sample)) == 6
    assert sum(s["counts"]["created"] for s in first["sources"]) == 6
    assert sum(s["counts"]["created"] for s in second["sources"]) == 0
    assert sum(s["counts"]["unchanged"] for s in second["sources"]) == 7


def test_rescheduled_performance_updates_correct_record(sample, capsys):
    load(sample, capsys)
    before = {r.key: r for r in records(sample)}
    update(sample)
    report = load(sample, capsys)
    after = {r.key: r for r in records(sample)}
    assert before.keys() == after.keys()
    suites = [r for r in after.values() if r.fields["title"] == "Suite del Río de la Plata"]
    assert len(suites) == 2
    assert all("21:00:00" in r.fields["starts_at"] for r in suites)
    assert sum(s["counts"]["updated"] for s in report["sources"]) == 2


def test_two_performances_keep_distinct_identities(sample, capsys):
    load(sample, capsys)
    candombe = [r for r in records(sample) if r.fields["title"] == "Candombe de Invierno"]
    assert len(candombe) == 2
    assert len({r.fields["starts_at"] for r in candombe}) == 2
    assert sorted(len(r.origins) for r in candombe) == [1, 2]


def test_source_down_keeps_previous_records_with_last_observation(sample, capsys):
    load(sample, capsys)
    before = records(sample)
    (sample / "tickantel-evento.html").write_text("<html>Source unavailable</html>")
    report = load(sample, capsys)
    after = records(sample)
    assert len(before) == len(after)
    stale = report["stale_sources"]
    assert len(stale) == 1
    source = stale[0]["source_url"]
    old = {r.key: r.origins[source] for r in before if source in r.origins}
    assert {tuple(k["identity"]) for k in stale[0]["affected_keys"]} == {
        ("candombe-1",),
        ("gala-tickantel",),
        ("suite-tickantel",),
    }
    assert datetime.fromisoformat(stale[0]["last_success"]) == max(
        origin.last_observed_at for origin in old.values()
    )
    assert stale[0]["last_success"] < stale[0]["last_attempt"]
    new = {r.key: r.origins[source] for r in after if source in r.origins}
    assert old == new


def test_uncertain_cross_source_match_keeps_both_origins_flagged(sample, capsys):
    load(sample, capsys)
    suites = [r for r in records(sample) if r.fields["title"] == "Suite del Río de la Plata"]
    assert len(suites) == 2
    assert all(r.uncertain_match for r in suites)
    assert len(set(suites[0].uncertain_match[0].origins)) == 2


def test_sale_date_not_loaded_as_event_date(sample, capsys):
    load(sample, capsys)
    for r in records(sample):
        assert r.fields["starts_at"].startswith("2026-11-")
        assert not r.fields["starts_at"].startswith("2026-11-01")
        for origin in r.origins.values():
            if "tickantel" in origin.source_url:
                assert origin.provenance["starts_at"].locator.endswith("/startDate")


def test_upcoming_query_returns_future_events_in_order(sample, capsys):
    load(sample, capsys)
    sql = Path("examples/uy-dance-agenda/queries/upcoming.sql").read_text()
    with closing(sqlite3.connect(sample / "agenda.sqlite")) as db:
        rows = db.execute(sql.replace("'now'", "'2026-11-07T00:00:00Z'")).fetchall()
    assert len(rows) == 6
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)


def test_job_load_honors_nondefault_match_tolerance(sample, capsys):
    path = sample / "job.toml"
    path.write_text(
        path.read_text().replace("match_tolerance_minutes = 90", "match_tolerance_minutes = 15")
    )
    load(sample, capsys)
    suites = [r for r in records(sample) if r.fields["title"] == "Suite del Río de la Plata"]
    assert len(suites) == 2
    assert all(not r.uncertain_match for r in suites)
