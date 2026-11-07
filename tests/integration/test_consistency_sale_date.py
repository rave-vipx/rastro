"""A recipe that reads the ticket sale date as the event date fails consistency (QUAL-005)."""

from __future__ import annotations

import json
import shutil
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rastro.application.consistency import evaluate_consistency
from rastro.cli import main
from rastro.domain.consistency import parse_consistency_rules

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "uy-dance-agenda"
OBSERVED = datetime(2026, 11, 7, 12, 0, tzinfo=timezone(timedelta(hours=-3)))
FIELDS = ["title", "performers", "venue", "city", "starts_at", "ticket_url"]


def _rules() -> Any:
    data = tomllib.loads((EXAMPLE / "sample" / "job.toml").read_text(encoding="utf-8"))
    return parse_consistency_rules(data["consistency"])


def _run(job: Path, capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    assert main(["job", "run", str(job), "--output", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    return [rec["data"] for res in report["results"] for rec in res["records"]]


def test_sale_date_recipe_fails_consistency_although_dates_are_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = tmp_path / "sample"
    shutil.copytree(EXAMPLE / "sample", sample)
    good = _run(sample / "job.toml", capsys)
    healthy = evaluate_consistency(_rules(), good, OBSERVED, declared_fields=FIELDS)
    assert healthy.verdict == "consistent"
    assert healthy.completeness == 1.0

    broken_job = (sample / "job.toml").read_text(encoding="utf-8")
    assert broken_job.count('starts_at = "/startDate"') == 2
    broken_job = broken_job.replace('starts_at = "/startDate"', 'starts_at = "/offers/validFrom"')
    (sample / "job.toml").write_text(broken_job, encoding="utf-8")

    bad = _run(sample / "job.toml", capsys)  # the dates still pass datetime validation
    assert len(bad) == len(good)
    report = evaluate_consistency(_rules(), bad, OBSERVED, declared_fields=FIELDS)
    assert report.verdict == "inconsistent"
    assert report.structural_validity == 1.0  # structurally valid ...
    assert report.completeness == 1.0  # ... and complete ...
    assert report.semantic_confidence is not None and report.semantic_confidence < 0.6
    assert [r.kind for r in report.violations] == ["after_observation"]
    # Per source, the sale date repeated across one seller's events collapses as well.
    seller = evaluate_consistency(_rules(), bad[4:], OBSERVED, declared_fields=FIELDS)
    assert {r.kind for r in seller.violations} == {"after_observation", "distinct_ratio"}


def test_example_rules_are_generic_kinds_only() -> None:
    assert {r.kind for r in _rules()} == {"after_observation", "distinct_ratio", "historical_shift"}
