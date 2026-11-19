"""The run report: why a source ended, rejected counts by code, hook payload, CLI output."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from rastro.application.run_report import report_for_load, report_for_run
from rastro.cli import main
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ExtractionResult, Issue, Metrics, Outcome
from rastro.domain.run_report import RunReport
from tests.integration.test_failure_hook import RECORD, hook
from tests.integration.test_open_drift_load import JOB, page
from tests.integration.test_spider_sitemap import XML, event, job, run, urlset

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Item(BaseModel):
    name: str


def synthetic(*codes: str, status: Outcome = Outcome.PARTIAL_SUCCESS) -> ExtractionResult[Item]:
    now = datetime.now(UTC)
    issues = tuple(Issue(code=code, record_index=i) for i, code in enumerate(codes))
    return ExtractionResult[Item](
        job_id="j",
        status=status,
        source_url="https://h.example/list?token=x#frag",
        locale="en-US",
        timezone="UTC",
        schema_fingerprint="0" * 64,
        started_at=now,
        completed_at=now,
        records=(),
        issues=issues,
        metrics=Metrics(
            input_bytes=1,
            candidates=3,
            accepted=0,
            rejected=len(codes),
            extraction_seconds=0,
            validation_seconds=0,
            total_seconds=0,
        ),
    )


def test_run_report_cap_vs_failure():
    locs = [f"/evento/{n}" for n in range(5)]
    pages = {"/sitemap.xml": (200, XML, urlset(*locs))} | {loc: event(loc) for loc in locs}
    capped = run(job(spider={"max_pages": 2}), pages)[0]
    report = report_for_run("job.toml", _wrap(capped))
    assert RunReport.model_validate_json(report.model_dump_json()) == report
    (source,) = report.sources
    assert (source.reason, source.detail) == ("page_cap", None)
    assert source.pages == 2

    failed = run(job(), {"/sitemap.xml": (500, XML, "")})[0]
    (source,) = report_for_run("job.toml", _wrap(failed)).sources
    assert (source.outcome.value, source.reason, source.detail) == (
        failed.status.value,
        "failed",
        "http_500",
    )
    assert source.host == "dance.test" and source.path == "/sitemap.xml"


def _wrap(result):
    return JobRunResult.from_results(schema_fingerprint="0" * 64, results=[result])


def test_load_report_counts_rejected_by_code():
    result = synthetic("validation_error", "validation_error", "invalid_text")
    run_result = _wrap(result)
    load = JobLoadResult.model_validate(
        {
            "dataset": "items",
            "job": run_result.job.model_dump(),
            "sources": [
                {
                    "source_url": result.source_url,
                    "status": "partial_success",
                    "counts": {"created": 0},
                }
            ],
            "stale_sources": [],
        }
    )
    report = report_for_load("job.toml", run_result, load)
    (source,) = report.sources
    assert source.rejected_by_code == {"validation_error": 2, "invalid_text": 1}
    assert source.records_rejected == 3 and source.stored == {"created": 0}
    assert report.kind == "load" and report.dataset == "items"
    assert (source.host, source.path) == ("h.example", "/list")


def test_report_backward_compatible_fields():
    run_result = _wrap(synthetic("validation_error"))
    old = run_result.model_dump(mode="json")
    assert "report" not in old  # absent until a producer attaches it
    assert JobRunResult[Item].model_validate(old).report is None
    load = {
        "dataset": "items",
        "job": old["job"],
        "sources": [{"source_url": "https://h.example/", "status": "success", "counts": {}}],
        "stale_sources": [],
    }
    loaded = JobLoadResult.model_validate(load)
    assert loaded.report is None and "report" not in loaded.model_dump(mode="json")
    assert set(loaded.model_dump(mode="json")) >= set(load) | {"status", "open_drift"}


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    url = "https://h.example/p?token=x"
    (tmp_path / "job.toml").write_text(JOB.replace("https://shop.example/list", url))
    (tmp_path / "page.html").write_text(page(2), encoding="utf-8")
    return tmp_path / "job.toml"


def test_hook_receives_run_report_without_query(spec, capsys):
    out = spec.parent / "summary.json"
    (spec.parent / "page.html").write_text("<html><body>nothing</body></html>")
    assert main(["job", "run", str(spec), "--on-failure", hook(RECORD, str(out))]) == 1
    text = out.read_text()
    payload = json.loads(text)
    assert payload["schema_version"] == 2 and payload["event"] == "rastro_job_failure"
    assert payload["status"] == "failed"  # the version 1 keys stay
    (source,) = payload["report"]["sources"]
    assert (source["host"], source["path"], source["reason"]) == (
        "h.example",
        "/p",
        "missing_data",
    )
    assert payload["report"]["job"] == "job.toml" and payload["report"]["exit_code"] == 1
    assert "token" not in text and "page.html" not in text


def test_cli_json_includes_report(spec, capsys):
    assert main(["job", "run", str(spec)]) == 0
    output = json.loads(capsys.readouterr().out)
    report = RunReport.model_validate(output["report"])
    assert (report.kind, report.status, report.job) == ("run", "success", "job.toml")
    assert report.totals.records_accepted == 2 and report.totals.succeeded == 1
    assert report.sources[0].reason == "complete" and "token" not in json.dumps(output["report"])
    assert main(["job", "load", str(spec)]) == 0
    loaded = json.loads(capsys.readouterr().out)["report"]
    assert loaded["kind"] == "load" and loaded["dataset"] == "items"
    assert loaded["sources"][0]["stored"]["created"] == 2
