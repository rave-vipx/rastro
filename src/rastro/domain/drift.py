"""Observable structural degradation, separate from extraction failure (DRIFT-001-006)."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from rastro.domain.models import Contract, Outcome

type Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FieldStructure(Contract):
    """None means unmeasured; zero is an observation, never a missing measurement."""

    selector_match_count: int | None = Field(default=None, ge=0, strict=True)
    fallback_used: bool | None = Field(default=None, strict=True)
    subtree_fingerprint: Digest | None = None
    field_coverage: Rate
    validation_failure_rate: Rate


class StructuralSnapshot(Contract):
    source_url: str
    run_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    schema_fingerprint: Digest
    locale: str = "und"
    timezone: str = "UTC"
    egress: str = "direct"
    status: Outcome
    fields: dict[str, FieldStructure] = Field(max_length=256)

    @property
    def context(self) -> tuple[str, ...]:
        return (self.source_url, self.schema_fingerprint, self.locale, self.timezone, self.egress)


class DriftThresholds(Contract):
    elevated: Rate = 0.2
    high: Rate = 0.5

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not 0 < self.elevated < self.high:
            raise ValueError("Drift thresholds require 0 < elevated < high <= 1")
        return self


type Indicator = Literal[
    "selector_match_count",
    "fallback_used",
    "subtree_fingerprint",
    "field_coverage",
    "validation_failure_rate",
]


class DriftSignal(Contract):
    field: str
    indicator: Indicator
    baseline: int | float | bool | str
    current: int | float | bool | str
    explanation: str
    dataset_effect: str
    contribution: Rate


class DriftAssessment(Contract):
    score: Rate
    risk: Literal["low", "elevated", "high"]
    verdict: Literal["structural_drift", "extraction_failure", "none"]
    signals: tuple[DriftSignal, ...]
    thresholds: DriftThresholds


_WEIGHTS: dict[Indicator, float] = {
    "selector_match_count": 0.35,
    "fallback_used": 0.15,
    "subtree_fingerprint": 0.2,
    "field_coverage": 0.2,
    "validation_failure_rate": 0.1,
}
_STRUCTURAL = {"selector_match_count", "fallback_used", "subtree_fingerprint"}
_UNAVAILABLE = {Outcome.ACCESS_RESTRICTION, Outcome.TRANSIENT_FAILURE, Outcome.PERMANENT_FAILURE}


def _signals(old: StructuralSnapshot, new: StructuralSnapshot) -> tuple[DriftSignal, ...]:
    signals = []
    effect = (
        "Validated records updated; monitor for further evidence loss."
        if new.status == Outcome.SUCCESS
        else "Rejected records not updated; earlier values and times kept; source flagged stale."
    )
    for name in sorted(old.fields.keys() & new.fields.keys()):
        before, after = old.fields[name], new.fields[name]
        for indicator, weight in _WEIGHTS.items():
            previous, current = getattr(before, indicator), getattr(after, indicator)
            if previous is None or current is None or previous == current:
                continue
            if indicator == "field_coverage":
                change = max(0.0, previous - current)
            elif indicator == "validation_failure_rate":
                change = max(0.0, current - previous)
            elif indicator == "fallback_used":
                change = float(current and not previous)
            elif indicator == "selector_match_count":
                change = min(1.0, abs(current - previous) / max(1, previous))
            else:
                change = 1.0
            if change:
                signals.append(
                    DriftSignal(
                        field=name,
                        indicator=indicator,
                        baseline=previous,
                        current=current,
                        explanation=f"{indicator}: weight {weight:g} * change {change:.6f}.",
                        dataset_effect=effect,
                        contribution=round(weight * change, 6),
                    )
                )
    return tuple(signals)


def assess_drift(
    baseline: StructuralSnapshot | None,
    current: StructuralSnapshot,
    *,
    thresholds: DriftThresholds | None = None,
) -> DriftAssessment:
    """Score the worst affected field against the last successful comparable run."""
    policy = thresholds or DriftThresholds()
    signals = (
        _signals(baseline, current)
        if baseline is not None
        and baseline.context == current.context
        and baseline.status == Outcome.SUCCESS
        and current.status not in _UNAVAILABLE
        else ()
    )
    scores: dict[str, float] = {}
    for signal in signals:
        scores[signal.field] = scores.get(signal.field, 0) + signal.contribution
    score = min(1.0, round(max(scores.values(), default=0), 6))
    structural = any(signal.indicator in _STRUCTURAL for signal in signals)
    return DriftAssessment(
        score=score,
        risk="high" if score >= policy.high else "elevated" if score >= policy.elevated else "low",
        verdict="structural_drift"
        if structural
        else "extraction_failure"
        if current.status != Outcome.SUCCESS
        else "none",
        signals=signals,
        thresholds=policy,
    )
