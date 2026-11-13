"""Offline load-repair-load and legacy snapshots (REPAIR-001, SCR-002)."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.repair_context import RepairContext
from rastro.cli import main
from rastro.domain.models import Limits

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
FIXTURES = Path(__file__).parents[1] / "fixtures/container_relocation"


def setup_job(tmp_path):
    (tmp_path / "third.html").write_text(
        (FIXTURES / "third.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    job = {
        "format_version": 1,
        "recipe_memory": "recipes.sqlite",
        "repair": {"auto_promote": True, "min_records": 1},
        "dataset": {"name": "events", "path": "events.sqlite", "identity": ["performance_id"]},
        "sources": [
            {
                "kind": "file",
                "path": "third.html",
                "url": "https://example.test/events",
                "recipe": {
                    "strategy": "css",
                    "record_selector": "article.dance",
                    "fields": {
                        "performance_id": "::attr(data-id)",
                        "title": "h2::text",
                        "venue": "p.venue::text",
                        "price": "span.price::text",
                    },
                },
            }
        ],
        "fields": [
            {"name": field, "type": "string"}
            for field in ("performance_id", "title", "venue", "price")
        ],
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def test_cli_container_repair_promotes_and_next_load_stores_one_correct_row(tmp_path, capsys):
    job = setup_job(tmp_path)
    assert main(["job", "load", str(job), "--drift"]) == 0
    capsys.readouterr()
    context = RepairContext.load(job, False)
    _source, key, _recipe = context.source(1)
    assert context.repository.get(key).recipe.version == 1
    (tmp_path / "third.html").write_text(
        (FIXTURES / "third-broken.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert main(["job", "load", str(job), "--drift"]) == 1
    capsys.readouterr()
    assert main(["recipe", "repair", str(job), "--source", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    comparison = report["sources"][0]["comparisons"][0]
    assert comparison["evaluation"]["decision"] == "promote"
    assert comparison["evaluation"]["historical_passed"] == 1
    active = context.repository.get(key).recipe
    assert active.version == comparison["candidate_version"]
    assert active.record_selector == "article.dance, main > article.performance"
    assert main(["job", "load", str(job), "--drift"]) == 0
    capsys.readouterr()
    (record,) = SQLiteDatasetStore(tmp_path / "events.sqlite").records("events")
    assert record.fields == {
        "performance_id": "synthetic-1",
        "title": "Milonga sintética",
        "venue": "Sala sintética",
        "price": "280",
    }


def test_old_snapshot_without_record_fingerprint_loads_and_returns_no_container_candidate(
    tmp_path, capsys
):
    job = setup_job(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    context = RepairContext.load(job, False)
    source, _, recipe = context.source(1)
    snapshot, _ = context.fixtures(source, recipe)
    assert snapshot.record is not None
    with closing(sqlite3.connect(tmp_path / "events.sqlite")) as db:
        saved = json.loads(db.execute("SELECT snapshot FROM relocation_samples").fetchone()[0])
        saved.pop("record")
        db.execute("UPDATE relocation_samples SET snapshot = ?", (json.dumps(saved),))
        db.commit()
        assert db.execute("SELECT version FROM relocation_schema").fetchall() == [(1,)]
    snapshot, fixtures = context.fixtures(source, recipe)
    assert snapshot.record is None
    assert len(fixtures) == 1
    changed = (FIXTURES / "third-broken.html").read_text(encoding="utf-8")
    assert ScraplingRelocation().relocate(changed, recipe, snapshot, Limits()) == ()
