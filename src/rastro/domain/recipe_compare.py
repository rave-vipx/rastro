"""Versioned repair decisions and comparisons (REPAIR-002-005, QUAL-006)."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from rastro.domain.consistency import ConsistencyReport
from rastro.domain.job_contract import RedactedContract
from rastro.domain.models import Contract, Issue, Outcome, Recipe
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
    # Why current records failed: record index, field and issue code; never values.
    blocking: tuple[Issue, ...] = Field(default=(), exclude_if=lambda v: not v)


_SETTINGS = ("record_selector", "record_path", "item_type", "require_evidence")


class RecipeComparison(Contract):
    schema_version: Literal[1] = 1
    previous_version: int
    candidate_version: int
    previous_digest: str
    candidate_digest: str
    changed_fields: tuple[str, ...]
    # Recipe-level locators that changed, e.g. ``record_selector`` (field names excluded).
    changed_settings: tuple[str, ...] = ()
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
            changed_settings=tuple(
                name
                for name in _SETTINGS
                if getattr(previous, name, None) != getattr(candidate, name, None)
            ),
            strategy_changed=previous.strategy != candidate.strategy,
            evaluation=evaluation,
        )


class SourceRepair(Contract):
    """Repair or promotion outcome for one job source (1-based ``source_index``).

    ``promoted_version`` is the version that became active in this call, or ``None``.
    ``outcome``, ``inference_calls`` and ``issues`` describe candidate generation and are
    absent for a promotion.
    """

    source_index: int = Field(ge=1)
    outcome: Literal["candidates", "no_candidates"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    inference_calls: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    issues: tuple[Issue, ...] | None = Field(default=None, exclude_if=lambda v: v is None)
    comparisons: tuple[RecipeComparison, ...]
    promoted_version: int | None = None


class RepairReport(Contract):
    """Versioned report of ``repair_job_source`` and ``promote_job_recipe``."""

    schema_version: Literal[1] = 1
    run_id: str
    sources: tuple[SourceRepair, ...] = Field(min_length=1)

    @property
    def exit_code(self) -> int:
        """0 when every source promoted a version, 3 when one was quarantined, else 1."""
        if all(source.promoted_version is not None for source in self.sources):
            return 0
        decisions = {
            c.evaluation.decision
            for s in self.sources
            for c in s.comparisons
            if c.evaluation is not None
        }
        return 3 if "quarantine" in decisions else 1
