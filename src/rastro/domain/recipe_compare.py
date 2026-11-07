"""Versioned repair decisions and comparisons (REPAIR-002-005, QUAL-006)."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from rastro.domain.consistency import ConsistencyReport
from rastro.domain.job_contract import RedactedContract
from rastro.domain.models import Contract, Outcome, Recipe
from rastro.domain.repair import recipe_digest

Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class RepairPolicy(RedactedContract):
    """Automatic promotion requires explicit opt-in and every measured gate."""

    auto_promote: StrictBool = False
    min_records: StrictInt = Field(default=1, ge=1, le=100_000)
    min_historical_fixtures: StrictInt = Field(default=1, ge=1, le=32)
    min_completeness: Rate = 1.0
    min_structural_validity: Rate = 1.0
    min_semantic_confidence: Rate = 1.0
    fixtures: tuple[str, ...] = Field(default=(), max_length=31)


class RegressionCheck(Contract):
    name: str
    passed: bool


class CandidateEvaluation(Contract):
    schema_version: Literal[1] = 1
    decision: Literal["promote", "quarantine", "reject"]
    reasons: tuple[str, ...]
    current_status: Outcome
    current_records: int = Field(ge=0)
    historical_fixtures: int = Field(ge=0)
    historical_passed: int = Field(ge=0)
    indicators: ConsistencyReport
    regressions: tuple[RegressionCheck, ...]


class RecipeComparison(Contract):
    schema_version: Literal[1] = 1
    previous_version: int
    candidate_version: int
    previous_digest: str
    candidate_digest: str
    changed_fields: tuple[str, ...]
    strategy_changed: bool
    evaluation: CandidateEvaluation | None = None

    @classmethod
    def between(
        cls, previous: Recipe, candidate: Recipe, evaluation: CandidateEvaluation | None = None
    ) -> "RecipeComparison":
        return cls(
            previous_version=previous.version,
            candidate_version=candidate.version,
            previous_digest=recipe_digest(previous),
            candidate_digest=recipe_digest(candidate),
            changed_fields=tuple(
                sorted(
                    name
                    for name in previous.fields.keys() | candidate.fields.keys()
                    if previous.fields.get(name) != candidate.fields.get(name)
                )
            ),
            strategy_changed=previous.strategy != candidate.strategy,
            evaluation=evaluation,
        )
