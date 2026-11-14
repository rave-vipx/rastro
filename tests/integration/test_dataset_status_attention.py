"""Attention exits non-zero; failures suggest repair and keep a reason (DATA-004/005)."""

import json

import pytest

from rastro.cli import main
from tests.integration.test_open_drift_load import JOB, page


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(3), encoding="utf-8")
    return tmp_path / "job.toml"


def test_missing_data_suggests_repair_and_dataset_status_exits_4(job, capsys):
    data = str(job.parent / "data.sqlite")
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    assert main(["dataset", "status", data]) == 0
    assert json.loads(capsys.readouterr().out)["open_drift"] == []
    (job.parent / "page.html").write_text("<html><body></body></html>", encoding="utf-8")
    assert main(["job", "load", str(job)]) == 1
    (source,) = json.loads(capsys.readouterr().out)["sources"]
    assert source["status"] == "missing_data"
    assert source["next_action"] == "rastro recipe repair"
    assert "reason" not in source  # no issue code: the outcome alone explains it
    assert main(["dataset", "status", data]) == 4
    status = json.loads(capsys.readouterr().out)
    assert status["sources"][0]["reason"] == "missing_data"
    assert "detail" not in status["sources"][0]
    assert main(["dataset", "status", data, "--format", "jsonl"]) == 4


def test_load_records_the_failure_detail_of_a_denied_source(job):
    """DATA-005: a 403 is stored as reason access_restriction with detail http_403."""
    from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
    from rastro.application.dataset_load import load_results
    from rastro.application.job_loader import load_job
    from rastro.application.jobs import run_job
    from rastro.domain.job_results import JobRunResult
    from rastro.domain.models import Issue, Outcome

    spec = load_job(job)
    report = run_job(spec, base_dir=job.parent)
    denied = report.results[0].model_copy(
        update={
            "status": Outcome.ACCESS_RESTRICTION,
            "records": (),
            "issues": (Issue(code="access_restricted"),),
            "http_status": 403,
        }
    )
    report = JobRunResult.from_results(
        schema_fingerprint=report.job.schema_fingerprint, results=[denied]
    )
    store = SQLiteDatasetStore(job.parent / "data.sqlite")
    loaded = load_results(spec.dataset, [f.name for f in spec.fields], report, store)
    assert loaded.sources[0].reason == "http_403"
    status = store.source_status("items")[0]
    assert (status.reason, status.detail) == ("access_restriction", "http_403")
