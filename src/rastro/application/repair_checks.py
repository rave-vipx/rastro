"""Normalize an adaptive or model recipe into a repair candidate, and the value checks.

A candidate keeps the serving recipe's version, pagination and representation and always
requires evidence; the value checks reject one whose values change type or shape or come
only from another field's history.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from rastro.application.adaptive_candidates import PASSING_REASONS
from rastro.application.extraction import validate_recipe
from rastro.domain.models import ConfigurationError, Recipe
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison
from rastro.domain.repair import RepairCandidate
from rastro.domain.value_consistency import value_consistency

type Rows = list[dict[str, JsonValue]]

__all__ = ["_candidate", "check_values"]


def check_values(
    evaluation: CandidateEvaluation,
    recipe: Recipe,
    schema: type[BaseModel],
    previous: Recipe,
    rows: Callable[[Recipe], Rows],
    history: Sequence[Mapping[str, JsonValue]],
) -> CandidateEvaluation:
    """Reject a model candidate whose values do not look like the known-good ones.

    Checks the changed fields (every field when only the record container moved) against
    the retained samples with the deterministic value gate. A failure is a rejection, not
    a quarantine: model output is untrusted and the judge must never be its only gate.
    """
    if evaluation.decision == "reject":
        return evaluation
    changed = RecipeComparison.between(previous, recipe).changed_fields
    failed = value_consistency(rows(recipe), history, changed or tuple(schema.model_fields))
    if not failed:
        return evaluation
    kept = tuple(r for r in evaluation.reasons if r not in PASSING_REASONS)
    reasons = tuple(dict.fromkeys((*kept, *failed)))
    return evaluation.model_copy(update={"decision": "reject", "reasons": reasons})


def _candidate(
    proposal: Recipe,
    schema: type[BaseModel],
    previous: Recipe,
    origin: Literal["adaptive", "model"] = "model",
) -> RepairCandidate | None:
    if previous.pagination is not None and proposal.strategy != previous.strategy:
        return None
    data = proposal.model_dump()
    data.update(
        require_evidence=True,
        fallbacks=(),
        version=previous.version,
        pagination=previous.pagination,
    )
    try:
        recipe = validate_recipe(schema, Recipe.model_validate(data), previous.representation)
    except ValidationError:
        raise ConfigurationError("Invalid model repair candidate") from None
    baseline = previous.model_copy(update={"require_evidence": True, "fallbacks": ()})
    if recipe == baseline:
        return None
    rationale = "Model proposal" if origin == "model" else "Adaptive match on retained values"
    return RepairCandidate(
        recipe=recipe, origin=origin, rationale=rationale + "; requires recipe CI"
    )
