"""Generic semantic consistency rules and report (QUAL-005, QUAL-006).

Rules are declarative data, declared in a job as ``[[consistency]]`` entries. They name
fields and thresholds only; they know nothing about any one domain. Evaluation lives in
``rastro.application.consistency``.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StrictFloat, StrictInt, StrictStr, TypeAdapter

from rastro.domain.sources import Contract

FieldName = Annotated[StrictStr, Field(min_length=1, max_length=128)]
Ratio = Annotated[StrictFloat | StrictInt, Field(ge=0, le=1)]


class AfterObservation(Contract):
    """Datetimes of ``field`` must not lie before the observation time (minus tolerance)."""

    kind: Literal["after_observation"]
    field: FieldName
    tolerance_minutes: StrictInt = Field(default=0, ge=0, le=525_600)
    max_violation_ratio: Ratio = 0.0


class DistinctRatio(Contract):
    """Distinct values of ``field`` over record count must not fall below ``min_ratio``."""

    kind: Literal["distinct_ratio"]
    field: FieldName
    min_ratio: Annotated[StrictFloat | StrictInt, Field(gt=0, le=1)] = 0.5
    min_records: StrictInt = Field(default=3, ge=2, le=100_000)


class HistoricalShift(Contract):
    """Median of ``field`` must not shift more than ``max_shift`` versus dataset history.

    ``unit = "days"`` compares datetimes; ``unit = "ratio"`` compares numbers as
    ``|current - history| / |history|``.
    """

    kind: Literal["historical_shift"]
    field: FieldName
    max_shift: Annotated[StrictFloat | StrictInt, Field(gt=0, le=1_000_000)]
    unit: Literal["days", "ratio"] = "days"
    min_history: StrictInt = Field(default=3, ge=1, le=1_000_000)


type ConsistencyRule = Annotated[
    AfterObservation | DistinctRatio | HistoricalShift, Field(discriminator="kind")
]
CONSISTENCY_RULES = TypeAdapter(tuple[ConsistencyRule, ...])


def parse_consistency_rules(items: Sequence[Any]) -> tuple[ConsistencyRule, ...]:
    """Validate ``[[consistency]]`` entries; unknown keys and kinds are rejected."""
    return CONSISTENCY_RULES.validate_python(tuple(items))


class RuleStatus(StrEnum):
    PASSED = "passed"
    VIOLATED = "violated"
    NOT_ASSESSABLE = "not_assessable"


class RuleResult(Contract):
    """One rule's outcome. Explanations carry field names and numbers, never source values."""

    kind: str
    field: str
    status: RuleStatus
    assessed: int = Field(ge=0)
    observed: float | None = None
    threshold: float
    confidence: float | None = Field(default=None, ge=0, le=1)
    violating_indices: tuple[int, ...] = ()
    explanation: str


class ConsistencyReport(Contract):
    """Three separate indicators (QUAL-006); ``None`` means "not assessed", never 1.0.

    - ``completeness``: share of declared fields that carry a value.
    - ``structural_validity``: share of candidate records that passed validation.
    - ``semantic_confidence``: lowest confidence among assessable rules.
    """

    schema_version: Literal[1] = 1
    records: int = Field(ge=0)
    completeness: float | None = Field(ge=0, le=1)
    structural_validity: float | None = Field(ge=0, le=1)
    semantic_confidence: float | None = Field(ge=0, le=1)
    verdict: Literal["consistent", "inconsistent", "not_assessed"]
    results: tuple[RuleResult, ...]

    @property
    def violations(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.status is RuleStatus.VIOLATED)
