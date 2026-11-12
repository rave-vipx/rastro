"""Evaluate semantic consistency rules over candidate records (QUAL-005, QUAL-006).

Pure and deterministic: no clock, no I/O, no randomness. The caller supplies the observation
time and the dataset history, taken *before* the candidate snapshot is applied.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite
from statistics import median
from typing import Any, Literal

from rastro.domain.consistency import (
    AfterObservation,
    ConsistencyReport,
    ConsistencyRule,
    DistinctRatio,
    HistoricalShift,
    RuleResult,
    RuleStatus,
)
from rastro.domain.dataset import DatasetNotFoundError
from rastro.ports.dataset import DatasetStore

type Record = Mapping[str, Any]
type History = Mapping[str, Sequence[Any]]
_DIGITS = 4


def evaluate_consistency(
    rules: Sequence[ConsistencyRule],
    records: Sequence[Record],
    observed_at: datetime,
    *,
    declared_fields: Sequence[str] = (),
    history: History | None = None,
    rejected: int = 0,
    expected_rates: Mapping[str, float] | None = None,
) -> ConsistencyReport:
    """Score candidate ``records`` (field -> JSON value) against ``rules``.

    ``rejected`` counts candidates that failed validation, for the structural indicator.
    ``expected_rates`` caps the completeness each declared field must reach: a field is
    complete when its share of filled records reaches its expected rate (default 1.0),
    and a field with an expected rate of 0 is not counted.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if rejected < 0:
        raise ValueError("rejected must not be negative")
    hist = history or {}
    results = tuple(_evaluate(rule, records, observed_at, hist) for rule in rules)
    scored = [r.confidence for r in results if r.confidence is not None]
    violated = any(r.status is RuleStatus.VIOLATED for r in results)
    verdict: Literal["consistent", "inconsistent", "not_assessed"] = (
        "inconsistent" if violated else "consistent" if scored else "not_assessed"
    )
    total = len(records) + rejected
    return ConsistencyReport(
        records=len(records),
        completeness=_completeness(records, declared_fields, expected_rates or {}),
        structural_validity=_round(len(records) / total) if total else None,
        semantic_confidence=min(scored) if scored else None,
        verdict=verdict,
        results=results,
    )


def history_from_store(store: DatasetStore, dataset: str, fields: Sequence[str]) -> History:
    """Collect stored values per field. Call before applying the candidate snapshot."""
    try:
        stored = store.records(dataset)
    except DatasetNotFoundError:
        stored = ()
    return {name: [r.fields[name] for r in stored if name in r.fields] for name in fields}


def _evaluate(
    rule: ConsistencyRule, records: Sequence[Record], observed_at: datetime, history: History
) -> RuleResult:
    if isinstance(rule, AfterObservation):
        return _after_observation(rule, records, observed_at)
    if isinstance(rule, DistinctRatio):
        return _distinct_ratio(rule, records)
    return _historical_shift(rule, records, history)


def _after_observation(
    rule: AfterObservation, records: Sequence[Record], observed_at: datetime
) -> RuleResult:
    limit = observed_at - timedelta(minutes=rule.tolerance_minutes)
    parsed = [(i, _instant(r.get(rule.field))) for i, r in enumerate(records)]
    seen = [(i, v) for i, v in parsed if v is not None]
    if not seen:
        return _unassessable(rule, rule.max_violation_ratio, "no datetime values to compare")
    early = tuple(i for i, v in seen if v < limit)
    ratio = _round(len(early) / len(seen))
    ok = ratio <= rule.max_violation_ratio
    return _result(
        rule,
        ok,
        len(seen),
        ratio,
        float(rule.max_violation_ratio),
        _round(1 - ratio) if not ok else 1.0,
        early,
        f"{len(early)} of {len(seen)} '{rule.field}' values lie before the observation time"
        f" (tolerance {rule.tolerance_minutes} min, allowed ratio {rule.max_violation_ratio})",
    )


def _distinct_ratio(rule: DistinctRatio, records: Sequence[Record]) -> RuleResult:
    values = [r[rule.field] for r in records if r.get(rule.field) not in (None, "")]
    if len(values) < rule.min_records:
        return _unassessable(
            rule, rule.min_ratio, f"fewer than {rule.min_records} '{rule.field}' values"
        )
    distinct = len({json.dumps(v, sort_keys=True, default=str) for v in values})
    ratio = _round(distinct / len(values))
    ok = ratio >= rule.min_ratio
    return _result(
        rule,
        ok,
        len(values),
        ratio,
        float(rule.min_ratio),
        _round(min(1.0, ratio / rule.min_ratio)),
        (),
        f"{distinct} distinct '{rule.field}' values over {len(values)} records"
        f" (ratio {ratio}, required at least {rule.min_ratio})",
    )


def _historical_shift(
    rule: HistoricalShift, records: Sequence[Record], history: History
) -> RuleResult:
    parse: Callable[[Any], Any] = _epoch if rule.unit == "days" else _number
    current = [v for r in records if (v := parse(r.get(rule.field))) is not None]
    past = [v for h in history.get(rule.field, ()) if (v := parse(h)) is not None]
    if not current or len(past) < rule.min_history:
        return _unassessable(
            rule, rule.max_shift, f"needs current values and {rule.min_history} history values"
        )
    shift = _shift(rule.unit, median(current), median(past))
    if shift is None:
        return _unassessable(rule, rule.max_shift, "history median is zero")
    ok = shift <= rule.max_shift
    return _result(
        rule,
        ok,
        len(current),
        shift,
        float(rule.max_shift),
        1.0 if ok else _round(rule.max_shift / shift),
        (),
        f"median '{rule.field}' shifted {shift} {rule.unit} versus {len(past)} history values"
        f" (allowed {rule.max_shift})",
    )


def _shift(unit: str, current: Any, past: Any) -> float | None:
    if unit == "days":
        return _round(abs(current - past) / 86_400)
    return None if past == 0 else _round(abs(current - past) / abs(past))


def _result(
    rule: ConsistencyRule,
    ok: bool,
    assessed: int,
    observed: float,
    threshold: float,
    confidence: float,
    indices: tuple[int, ...],
    explanation: str,
) -> RuleResult:
    return RuleResult(
        kind=rule.kind,
        field=rule.field,
        status=RuleStatus.PASSED if ok else RuleStatus.VIOLATED,
        assessed=assessed,
        observed=observed,
        threshold=threshold,
        confidence=1.0 if ok else confidence,
        violating_indices=indices,
        explanation=explanation,
    )


def _unassessable(rule: ConsistencyRule, threshold: float, why: str) -> RuleResult:
    return RuleResult(
        kind=rule.kind,
        field=rule.field,
        status=RuleStatus.NOT_ASSESSABLE,
        assessed=0,
        threshold=float(threshold),
        explanation=f"'{rule.field}' not assessed: {why}",
    )


def _completeness(
    records: Sequence[Record], declared: Sequence[str], expected: Mapping[str, float]
) -> float | None:
    scores = []
    for name in declared:
        rate = expected.get(name, 1.0)
        if records and rate > 0:
            filled = sum(1 for r in records if r.get(name) not in (None, "")) / len(records)
            scores.append(min(1.0, filled / rate))
    return _round(sum(scores) / len(scores)) if scores else None


def _instant(value: Any) -> datetime | None:
    """Aware datetimes only; a naive or unparsable value is not assessable, never guessed."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    return None


def _epoch(value: Any) -> float | None:
    moment = _instant(value)
    return None if moment is None else moment.timestamp()


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and isfinite(float(number)) else None


def _round(value: float | Decimal) -> float:
    return round(float(value), _DIGITS)
