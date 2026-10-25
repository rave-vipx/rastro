"""Multiset scoring against independently supplied expected records (DIFF-001)."""

from collections import Counter
from collections.abc import Iterable
from math import ceil
from typing import TypedDict

from benchmarks.contracts import Case, Listing, Observation, values


def _fields(records: Iterable[Listing]) -> Counter[tuple[str, str, str]]:
    return Counter(
        (record.sku, key, value) for record in records for key, value in values(record).items()
    )


def _records(records: Iterable[Listing]) -> Counter[tuple[tuple[str, str], ...]]:
    return Counter(tuple(sorted(values(record).items())) for record in records)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


class Score(TypedDict):
    field_tp: int
    field_fp: int
    field_fn: int
    field_precision: float | None
    field_recall: float | None
    correct_records: int
    expected_records: int
    exact_records: bool
    announced_success: bool
    false_success: bool
    requires_warning: bool
    warning_detected: bool


def score(case: Case, observation: Observation) -> Score:
    predicted, expected = _fields(observation.records), _fields(case.expected)
    correct = sum((predicted & expected).values())
    exact = _records(observation.records) == _records(case.expected)
    announced = observation.status == "success"
    return {
        "field_tp": correct,
        "field_fp": sum((predicted - expected).values()),
        "field_fn": sum((expected - predicted).values()),
        "field_precision": ratio(correct, sum(predicted.values())),
        "field_recall": ratio(correct, sum(expected.values())),
        "correct_records": sum((_records(observation.records) & _records(case.expected)).values()),
        "expected_records": len(case.expected),
        "exact_records": exact,
        "announced_success": announced,
        "false_success": announced and (not exact or case.requires_warning),
        "requires_warning": case.requires_warning,
        "warning_detected": case.requires_warning and not announced,
    }


def percentile95(samples: list[float]) -> float:
    return sorted(samples)[ceil(0.95 * len(samples)) - 1]
