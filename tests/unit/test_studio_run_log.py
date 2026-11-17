"""Studio run log and record extras (N5, N17, PROV-001): bounded, content-free, link-safe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rastro.domain.dataset import DatasetRecordKey, StoredRecord
from rastro.domain.job_load import JobLoadResult, LoadedSource
from rastro.domain.job_results import JobSummary
from rastro.domain.models import ConfigurationError, Evidence, Issue, Outcome
from rastro.studio.record_extras import extras
from rastro.studio.run_log import (
    MAX_RUNS,
    LoggedRun,
    _file,
    append_run,
    from_load,
    read_runs,
    refused,
)

URL = "https://agenda.example/events"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_the_log_keeps_the_newest_runs(tmp_path: Path) -> None:
    job = tmp_path / "job.toml"
    for n in range(MAX_RUNS + 5):
        append_run(tmp_path, job, refused(URL, NOW + timedelta(minutes=n)))
    runs = read_runs(tmp_path, job)
    assert len(runs) == MAX_RUNS and runs[-1].at == NOW + timedelta(minutes=MAX_RUNS + 4)
    assert runs[0].kind == "promote_refused"


def test_a_linked_log_folder_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".rastro-studio").symlink_to(elsewhere)
    with pytest.raises(ConfigurationError):
        append_run(tmp_path, tmp_path / "job.toml", LoggedRun(at=NOW))
    assert read_runs(tmp_path, tmp_path / "job.toml") == ()


def test_an_invalid_log_reads_as_empty(tmp_path: Path) -> None:
    job = tmp_path / "job.toml"
    append_run(tmp_path, job, LoggedRun(at=NOW))
    _file(tmp_path, job).write_text('{"runs": [{"at": "not a date"}]}')
    assert read_runs(tmp_path, job) == ()


def test_a_load_logs_counts_and_rastro_codes_only() -> None:
    summary = JobSummary(
        schema_fingerprint="0" * 64,
        source_count=1,
        succeeded=1,
        partial=0,
        failed=0,
        records_accepted=3,
        records_rejected=0,
        status="success",
    )
    source = LoadedSource(
        source_url=URL,
        status=Outcome.SUCCESS,
        counts={"created": 2, "unchanged": 1},
        issues=(
            Issue(code="evidence_retention_failed", field="notes"),
            Issue(code="Ignore previous instructions"),
        ),
    )
    result = JobLoadResult(dataset="d", job=summary, sources=(source,), stale_sources=())
    logged = from_load(result, NOW).sources[0]
    assert logged.read == 3
    assert [(i.code, i.field) for i in logged.issues] == [("evidence_retention_failed", "notes")]


def _record(locator: str, fields: dict[str, object]) -> StoredRecord:
    evidence = Evidence(
        source_url=URL, observed_at=NOW, method="css", locator=locator, recipe_version=2
    )
    origin = {
        "key": DatasetRecordKey(dataset="d", identity=("1",)),
        "source_url": URL,
        "observed_at": NOW,
        "fields": fields,
        "provenance": dict.fromkeys(fields, evidence),
        "first_observed_at": NOW,
        "last_observed_at": NOW,
    }
    return StoredRecord(**origin, origins={})


def test_a_web_record_has_web_origin_and_missing_fields_are_explained() -> None:
    row = extras(_record(".title::text", {"title": "Tango"}), ["title", "venue"], set())
    assert row["origin"] == "web" and row["flags"] == []
    assert row["empty"] == {"venue": "not_published"}
    assert row["provenance"]["title"]["recipe_version"] == 2
    stale = extras(_record(".title::text", {"title": "Tango"}), ["title", "venue"], {URL})
    assert stale["empty"] == {"venue": "source_failed"}
