"""Offline integration tests for the Uruguay dance agenda pilot example (COST-001)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from rastro.cli import main

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = ROOT / "examples" / "uy-dance-agenda"
LIVE_JOB = EXAMPLE_DIR / "job.toml"
SAMPLE_JOB = EXAMPLE_DIR / "sample" / "job.toml"
SAMPLE_V2_JOB = EXAMPLE_DIR / "sample" / "v2" / "job.toml"

EXPECTED_FIELDS = [
    "title",
    "performers",
    "venue",
    "city",
    "starts_at",
    "ticket_url",
    "price",
    "currency",
]
SAMPLE_FIELDS = [*EXPECTED_FIELDS, "performance_id"]
LIVE_FIELDS = [
    "title",
    "performers",
    "category",
    "venue",
    "city",
    "starts_on",
    "starts_at",
    "ticket_url",
    "price",
    "currency",
]


def _run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    exit_code = main(list(args))
    captured = capsys.readouterr()
    assert captured.err == ""
    payload: dict[str, Any] = json.loads(captured.out)
    return exit_code, payload


def test_uy_dance_jobs_validate_offline_with_stable_sample_schema(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Network access is forbidden in offline integration tests")

    monkeypatch.setattr(httpx, "Client", _forbid_network)

    fingerprints: set[str] = set()
    for job_path in (LIVE_JOB, SAMPLE_JOB, SAMPLE_V2_JOB):
        code, validated = _run_cli(capsys, "job", "validate", str(job_path))
        assert code == 0
        assert validated["valid"] is True
        assert validated["format_version"] == 1
        assert validated["source_count"] == (4 if job_path == LIVE_JOB else 2)
        assert validated["fields"] == (LIVE_FIELDS if job_path == LIVE_JOB else SAMPLE_FIELDS)
        assert validated["job"]["region"] == {
            "locale": "es-UY",
            "languages": ["es-UY", "es"],
            "timezone": "America/Montevideo",
            "currency": "UYU",
            "egress": "direct",
        }
        if job_path != LIVE_JOB:
            fingerprints.add(validated["schema_fingerprint"])

    assert len(fingerprints) == 1


def test_uy_dance_sample_job_runs_offline_with_expected_counts(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Network access is forbidden in offline integration tests")

    monkeypatch.setattr(httpx, "Client", _forbid_network)

    code, report = _run_cli(capsys, "job", "run", str(SAMPLE_JOB), "--output", "json")
    assert code == 0
    assert report["data_trust"] == "untrusted_source_data"
    assert report["job"]["status"] == "success"
    assert report["job"]["source_count"] == 2
    assert report["job"]["succeeded"] == 2
    assert report["job"]["partial"] == 0
    assert report["job"]["failed"] == 0
    assert report["job"]["records_accepted"] == 7
    assert report["job"]["records_rejected"] == 0

    venue_result, seller_result = report["results"]
    assert venue_result["status"] == "success"
    assert venue_result["source_url"] == "https://sodre.gub.uy/espectaculos/"
    assert venue_result["metrics"]["acquisition_method"] == "provided_html"
    assert venue_result["metrics"]["inference_calls"] == 0
    assert venue_result["metrics"]["accepted"] == 4
    assert venue_result["metrics"]["rejected"] == 0

    assert seller_result["status"] == "success"
    assert (
        seller_result["source_url"]
        == "https://tickantel.com.uy/ng/espectaculo/9900102/gala-anual-2026-area-tango-y-milonga"
    )
    assert seller_result["metrics"]["acquisition_method"] == "provided_html"
    assert seller_result["metrics"]["inference_calls"] == 0
    assert seller_result["metrics"]["accepted"] == 3
    assert seller_result["metrics"]["rejected"] == 0

    for source_result in (venue_result, seller_result):
        for record in source_result["records"]:
            assert set(record["data"].keys()) == set(SAMPLE_FIELDS)
            assert set(record["evidence"].keys()) == set(SAMPLE_FIELDS)


def test_uy_dance_sample_covers_repeated_rescheduled_near_match_and_sale_date_cases(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Network access is forbidden in offline integration tests")

    monkeypatch.setattr(httpx, "Client", _forbid_network)

    code_v1, report_v1 = _run_cli(capsys, "job", "run", str(SAMPLE_JOB), "--output", "json")
    code_v2, report_v2 = _run_cli(capsys, "job", "run", str(SAMPLE_V2_JOB), "--output", "json")
    assert code_v1 == 0
    assert code_v2 == 0

    venue_v1 = [rec["data"] for rec in report_v1["results"][0]["records"]]
    seller_v1 = [rec["data"] for rec in report_v1["results"][1]["records"]]
    venue_v2 = [rec["data"] for rec in report_v2["results"][0]["records"]]
    seller_v2 = [rec["data"] for rec in report_v2["results"][1]["records"]]

    # 1. Performance repeated on two dates (same title and venue, distinct starts_at dates).
    candombe_dates = [
        rec["starts_at"]
        for rec in venue_v1
        if rec["title"] == "Candombe de Invierno" and rec["venue"] == "Auditorio Nelly Goitiño"
    ]
    assert candombe_dates == [
        "2026-11-20T20:30:00-03:00",
        "2026-11-21T20:30:00-03:00",
    ]

    # 2. Rescheduled performance in sample/v2/ (time changed on both sources).
    suite_venue_v1 = next(rec for rec in venue_v1 if rec["title"] == "Suite del Río de la Plata")
    suite_seller_v1 = next(rec for rec in seller_v1 if rec["title"] == "Suite del Río de la Plata")
    suite_venue_v2 = next(rec for rec in venue_v2 if rec["title"] == "Suite del Río de la Plata")
    suite_seller_v2 = next(rec for rec in seller_v2 if rec["title"] == "Suite del Río de la Plata")
    assert suite_venue_v1["starts_at"] == "2026-11-28T20:00:00-03:00"
    assert suite_seller_v1["starts_at"] == "2026-11-28T20:30:00-03:00"
    assert suite_venue_v2["starts_at"] == "2026-11-28T21:00:00-03:00"
    assert suite_seller_v2["starts_at"] == "2026-11-28T21:00:00-03:00"

    # 3. Same event listed by both sources with slightly different titles (near-match).
    venue_gala = next(rec for rec in venue_v1 if "Gala Anual" in rec["title"])
    seller_gala = next(rec for rec in seller_v1 if "Gala Anual" in rec["title"])
    assert venue_gala["title"] == "Gala Anual 26: Tango y Milonga"
    assert seller_gala["title"] == "Gala Anual 2026: Área Tango y Milonga"
    assert venue_gala["venue"] == seller_gala["venue"] == "Auditorio Nelly Goitiño"
    assert venue_gala["starts_at"] == seller_gala["starts_at"] == "2026-11-26T20:00:00-03:00"

    # 4. Ticket sale date vs event date: fixture embeds validFrom = 2026-11-01T10:00:00-03:00,
    #    and extraction selects startDate (event date) for every record.
    tickantel_html = (EXAMPLE_DIR / "sample" / "tickantel-evento.html").read_text(encoding="utf-8")
    assert "2026-11-01T10:00:00-03:00" in tickantel_html
    for rec in seller_v1:
        assert rec["starts_at"] != "2026-11-01T10:00:00-03:00"
        assert rec["starts_at"].startswith("2026-11-2")


def test_sodre_sample_now_accepts_records():
    from rastro import Limits, Source
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.application.extraction import extract_document
    from rastro.domain.jobs import parse_job

    job = parse_job(SAMPLE_JOB.read_text(encoding="utf-8"), format="toml")
    source = job.sources[0]
    recipe = source.recipe
    assert recipe.fields["starts_at"] == "time.cyg--dia::text"
    result = extract_document(
        (SAMPLE_JOB.parent / "sodre-cartelera.html").read_text(encoding="utf-8"),
        source=Source(url=source.url, **job.region.model_dump()),
        schema=job.build_schema(),
        recipe=recipe,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    assert result.metrics.accepted == 4
    assert result.records[0].evidence["starts_at"].locator.endswith("time.cyg--dia::text")
