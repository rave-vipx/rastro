"""A removed or renamed source stops counting and can be forgotten (DATA-004, SYNC-008)."""

import json
import sqlite3
from contextlib import closing

import pytest

import rastro
from rastro.cli import main
from tests.integration.test_open_drift_load import JOB, page

OLD = "https://shop.example/list"
NEW = "https://shop.example/new-list"


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(3), encoding="utf-8")
    return tmp_path / "job.toml"


def counts(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("dataset_sources", "drift_runs", "drift_open", "relocation_samples")
        }


def test_renamed_source_stops_being_stale_and_forget_source_removes_its_state(job, capsys):
    load = ["job", "load", str(job), "--drift"]
    assert main(load) == 0
    (job.parent / "page.html").write_text("<html><body></body></html>", encoding="utf-8")
    assert main(load) == 1
    (job.parent / "page.html").write_text(page(3), encoding="utf-8")
    job.write_text(job.read_text().replace(OLD, NEW), encoding="utf-8")
    capsys.readouterr()
    assert main(load) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stale_sources"] == [] and report["status"] == "success"
    before = counts(job.parent / "data.sqlite")
    assert before["dataset_sources"] == 2 and before["relocation_samples"] == 2

    assert main(["job", "forget-source", str(job), "--url", OLD]) == 0
    forgotten = json.loads(capsys.readouterr().out)
    assert forgotten["source_url"] == OLD
    assert forgotten["removed"] == {
        "dataset_sources": 1,
        "drift_runs": 2,
        "open_drift": 0,
        "relocation_samples": 1,
    }
    after = counts(job.parent / "data.sqlite")
    assert after == {
        "dataset_sources": 1,
        "drift_runs": 1,
        "drift_open": 0,
        "relocation_samples": 1,
    }
    assert rastro.forget_job_source(job, OLD)["removed"]["dataset_sources"] == 0


def test_forget_source_refuses_a_current_source(job, capsys):
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    assert main(["job", "forget-source", str(job), "--url", OLD]) == 2
    assert json.loads(capsys.readouterr().err)["location"] == "url"
