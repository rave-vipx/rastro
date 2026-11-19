"""Run report: the versioned, content-free summary of one run or load."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from rastro.domain.run_report import (
    RUN_REPORT_SCHEMA_ID,
    RunReport,
    RunTotals,
    SourceReport,
    SourceTiming,
    run_report_json_schema,
)

START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def source(**changes: Any) -> SourceReport:
    values: dict[str, Any] = {
        "source_index": 1,
        "host": "shop.example",
        "path": "/catalog",
        "outcome": "success",
        "reason": "complete",
        "pages": 1,
        "records_accepted": 4,
        "seconds": SourceTiming(total=1.5),
    }
    return SourceReport(**(values | changes))


def totals(**changes: Any) -> RunTotals:
    values = dict.fromkeys(RunTotals.model_fields, 0) | {"sources": 1, "succeeded": 1}
    return RunTotals(**(values | changes))


def report(**changes: Any) -> RunReport:
    values: dict[str, Any] = {
        "kind": "run",
        "job": "catalog.toml",
        "started_at": START,
        "completed_at": START + timedelta(seconds=2),
        "status": "success",
        "exit_code": 0,
        "sources": (source(),),
        "totals": totals(),
    }
    return RunReport(**(values | changes))


def _property_names(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            names |= _property_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _property_names(item)
    return names


def test_run_report_schema_has_no_record_fields():
    schema = run_report_json_schema()
    names = _property_names(schema)
    assert {"records", "data", "body", "query", "url"}.isdisjoint(names)
    assert {"records_accepted", "rejected_by_code", "capabilities_used"} <= names


def test_schema_is_draft_2020_12_with_a_stable_id():
    schema = run_report_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == RUN_REPORT_SCHEMA_ID
    assert RUN_REPORT_SCHEMA_ID.endswith("/schemas/run-report.schema.json")


def test_a_minimal_report_round_trips_as_json():
    built = report()
    assert built.schema_version == 1
    assert built.content == "content_free"
    assert RunReport.model_validate_json(built.model_dump_json()) == built


def test_source_paths_never_carry_a_query_or_fragment():
    for path in ("/search?q=shoes", "/page#top", "catalog", "https://shop.example/"):
        with pytest.raises(ValidationError):
            source(path=path)


def test_source_hosts_are_bare_lowercase_names():
    for host in ("user:pw@shop.example", "Shop.Example", "shop.example/path", ""):
        with pytest.raises(ValidationError):
            source(host=host)


def test_detail_and_rejected_codes_are_code_shaped():
    assert source(detail="http_403").detail == "http_403"
    with pytest.raises(ValidationError):
        source(detail="403 Forbidden from upstream")
    with pytest.raises(ValidationError):
        source(rejected_by_code={"Bad Value": 1})
    with pytest.raises(ValidationError):
        source(source_index=0)
    with pytest.raises(ValidationError):
        source(records_rejected=-1)


def test_stored_counts_use_change_kinds():
    assert source(stored={"created": 2}).stored == {"created": 2}
    with pytest.raises(ValidationError):
        source(stored={"teleported": 1})


def test_job_is_a_file_name_not_a_path():
    for job in ("/home/user/catalog.toml", "jobs/catalog.toml", "..\\catalog.toml", "", ".", ".."):
        with pytest.raises(ValidationError):
            report(job=job)


def test_times_are_aware_and_ordered():
    with pytest.raises(ValidationError):
        report(started_at=datetime(2026, 1, 2))
    with pytest.raises(ValidationError):
        report(completed_at=START - timedelta(seconds=1))


def test_totals_count_the_listed_sources():
    with pytest.raises(ValidationError):
        report(totals=totals(sources=2))
    with pytest.raises(ValidationError):
        report(sources=(source(), source()), totals=totals(sources=2))  # duplicate index


def test_exit_code_and_status_are_bounded():
    with pytest.raises(ValidationError):
        report(exit_code=256)
    with pytest.raises(ValidationError):
        report(status="mostly_fine")
    with pytest.raises(ValidationError):
        report(schema_version=2)
