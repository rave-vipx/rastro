"""Semantic consistency rules and the three separate indicators (QUAL-005, QUAL-006)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from rastro.application.consistency import evaluate_consistency, history_from_store
from rastro.domain.consistency import (
    AfterObservation,
    DistinctRatio,
    HistoricalShift,
    RuleStatus,
    parse_consistency_rules,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _rows(*starts: Any) -> list[dict[str, Any]]:
    return [{"title": f"t{i}", "starts_at": s} for i, s in enumerate(starts)]


def test_rules_parse_from_job_entries_and_reject_unknown_keys_and_kinds() -> None:
    rules = parse_consistency_rules(
        [
            {"kind": "after_observation", "field": "starts_at"},
            {"kind": "distinct_ratio", "field": "starts_at"},
            {"kind": "historical_shift", "field": "price", "max_shift": 0.5, "unit": "ratio"},
        ]
    )
    assert [type(r) for r in rules] == [AfterObservation, DistinctRatio, HistoricalShift]
    for bad in (
        {"kind": "after_observation", "field": "x", "extra": 1},
        {"kind": "mystery", "field": "x"},
        {"kind": "distinct_ratio", "field": "x", "min_ratio": 0},
        {"kind": "historical_shift", "field": "x"},
    ):
        with pytest.raises(ValidationError):
            parse_consistency_rules([bad])


def test_after_observation_flags_past_events_and_honours_tolerance() -> None:
    rule = AfterObservation(kind="after_observation", field="starts_at", tolerance_minutes=60)
    rows = _rows("2026-11-20T20:00:00-03:00", "2026-11-07T08:30:00+00:00", "2026-10-01T10:00:00Z")
    report = evaluate_consistency([rule], rows, NOW)
    result = report.results[0]
    assert report.verdict == "inconsistent"
    assert result.status is RuleStatus.VIOLATED
    assert result.violating_indices == (1, 2)
    assert result.observed == pytest.approx(0.6667)
    assert evaluate_consistency([rule], rows[:1], NOW).verdict == "consistent"
    lenient = rule.model_copy(update={"max_violation_ratio": 0.7})
    assert evaluate_consistency([lenient], rows, NOW).verdict == "consistent"


def test_distinct_ratio_detects_collapse_and_needs_enough_records() -> None:
    rule = DistinctRatio(kind="distinct_ratio", field="starts_at", min_ratio=0.5, min_records=3)
    collapsed = evaluate_consistency([rule], _rows("a", "a", "a"), NOW)
    assert collapsed.results[0].status is RuleStatus.VIOLATED
    assert collapsed.results[0].observed == pytest.approx(0.3333)
    assert evaluate_consistency([rule], _rows("a", "b", "c"), NOW).verdict == "consistent"
    few = evaluate_consistency([rule], _rows("a", "a"), NOW)
    assert few.results[0].status is RuleStatus.NOT_ASSESSABLE
    assert few.verdict == "not_assessed"


def test_historical_shift_compares_medians_for_days_and_ratios() -> None:
    days = HistoricalShift(kind="historical_shift", field="starts_at", max_shift=10, unit="days")
    past = {"starts_at": ["2026-11-10T00:00:00Z", "2026-11-12T00:00:00Z", "2026-11-14T00:00:00Z"]}
    near = _rows("2026-11-11T00:00:00Z", "2026-11-13T00:00:00Z")  # even count: no datetime math
    far = _rows("2026-12-20T00:00:00Z", "2026-12-22T00:00:00Z")
    assert evaluate_consistency([days], near, NOW, history=past).verdict == "consistent"
    shifted = evaluate_consistency([days], far, NOW, history=past)
    assert shifted.verdict == "inconsistent"
    assert shifted.results[0].observed == pytest.approx(39.0)
    ratio = HistoricalShift(kind="historical_shift", field="price", max_shift=0.5, unit="ratio")
    hist = {"price": ["100", 100, 100.0]}
    ok = evaluate_consistency([ratio], [{"price": "120"}], NOW, history=hist)
    bad = evaluate_consistency([ratio], [{"price": 400}], NOW, history=hist)
    assert (ok.verdict, bad.verdict) == ("consistent", "inconsistent")


def test_missing_history_or_values_is_not_assessed_not_consistent() -> None:
    rule = HistoricalShift(kind="historical_shift", field="starts_at", max_shift=5)
    assert evaluate_consistency([rule], _rows("2026-11-20T00:00:00Z"), NOW).verdict == (
        "not_assessed"
    )
    after = AfterObservation(kind="after_observation", field="starts_at")
    naive = evaluate_consistency([after], _rows("2026-11-20T10:00:00", None, "soon"), NOW)
    assert naive.results[0].status is RuleStatus.NOT_ASSESSABLE
    assert naive.semantic_confidence is None
    assert evaluate_consistency([], [], NOW).verdict == "not_assessed"


def test_indicators_are_separate() -> None:
    rule = AfterObservation(kind="after_observation", field="starts_at")
    rows = [
        {"title": "a", "starts_at": "2026-11-20T00:00:00Z", "city": None},
        {"title": "b", "starts_at": "2026-10-01T00:00:00Z", "city": "X"},
    ]
    report = evaluate_consistency(
        [rule], rows, NOW, declared_fields=["title", "starts_at", "city"], rejected=2
    )
    assert report.completeness == pytest.approx(0.8333)
    assert report.structural_validity == 0.5
    assert report.semantic_confidence == 0.5
    complete = [{"title": "a", "starts_at": "2026-10-01T00:00:00Z", "city": "X"}]
    other = evaluate_consistency([rule], complete, NOW, declared_fields=["title", "starts_at"])
    assert (other.completeness, other.structural_validity) == (1.0, 1.0)
    assert other.verdict == "inconsistent" and other.semantic_confidence == 0.0


def test_report_is_deterministic_and_leaks_no_values() -> None:
    rules = parse_consistency_rules(
        [
            {"kind": "after_observation", "field": "starts_at"},
            {"kind": "distinct_ratio", "field": "starts_at"},
        ]
    )
    rows = _rows(*["2026-10-01T10:00:00Z"] * 3)
    first = evaluate_consistency(rules, rows, NOW, declared_fields=["title"])
    second = evaluate_consistency(rules, list(rows), NOW, declared_fields=["title"])
    assert first.model_dump_json() == second.model_dump_json()
    assert all("2026-10-01" not in r.explanation for r in first.results)


def test_naive_observation_time_and_negative_rejections_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_consistency([], [], datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="negative"):
        evaluate_consistency([], [], NOW, rejected=-1)


def test_history_comes_from_store_records() -> None:
    class Stored:
        def __init__(self, fields: dict[str, Any]) -> None:
            self.fields = fields

    class FakeStore:
        def records(self, dataset: str) -> tuple[Stored, ...]:
            assert dataset == "d"
            return (Stored({"a": 1, "b": 2}), Stored({"a": 3}))

    history = history_from_store(FakeStore(), "d", ["a", "b", "c"])  # type: ignore[arg-type]
    assert history == {"a": [1, 3], "b": [2], "c": []}
