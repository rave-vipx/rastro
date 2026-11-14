"""A dataset failure after commits keeps the report and exits 5 (DATA-001, QUAL-005)."""

import json

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main
from rastro.cli_errors import classify
from rastro.domain.dataset import DatasetStorageError
from tests.integration.test_open_drift_load import JOB, page

SECOND = """
[[sources]]
kind = "file"
path = "page.html"
url = "https://shop.example/other"

[sources.recipe]
version = 1
strategy = "css"
record_selector = ".item"
require_evidence = true

[sources.recipe.fields]
sku = "::attr(data-sku)"
name = "h2::text"
"""


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB + SECOND, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(2), encoding="utf-8")
    return tmp_path / "job.toml"


def test_failure_after_first_commit_reports_committed_rows_and_exits_5(job, capsys, monkeypatch):
    original = SQLiteDatasetStore.apply_snapshot
    calls = []

    def flaky(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise DatasetStorageError("Dataset operation failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteDatasetStore, "apply_snapshot", flaky)
    assert main(["job", "load", str(job), "--drift"]) == 5
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "storage_error"
    first, second = report["sources"]
    assert first["counts"]["created"] == 2 and "issues" not in first
    assert [issue["code"] for issue in second["issues"]] == ["storage_error"]
    assert sum(second["counts"].values()) == 0
    assert len(report["drift"]) == 1
    assert len(SQLiteDatasetStore(job.parent / "data.sqlite").records("items")) == 2


def test_storage_errors_have_their_own_error_code():
    assert classify(DatasetStorageError("Dataset operation failed"))["error"] == "storage_error"


def test_drift_write_failure_keeps_committed_rows_visible(job, capsys, monkeypatch):
    from rastro.adapters.sqlite_drift import SQLiteDriftMonitor

    def broken(self, dataset, snapshot):
        raise DatasetStorageError("Dataset operation failed")

    monkeypatch.setattr(SQLiteDriftMonitor, "assess", broken)
    assert main(["job", "load", str(job), "--drift"]) == 5
    report = json.loads(capsys.readouterr().out)
    first, second = report["sources"]
    assert first["counts"]["created"] == 2
    assert [issue["code"] for issue in first["issues"]] == ["drift_storage_error"]
    assert [issue["code"] for issue in second["issues"]] == ["storage_error"]
    assert report["drift"] == []
