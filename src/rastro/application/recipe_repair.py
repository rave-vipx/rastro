"""Candidate-only persistence followed by measured lifecycle decisions (REC-006)."""

from collections.abc import Callable, Sequence
from datetime import datetime

from rastro.application.intelligence import with_version
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey
from rastro.domain.models import Recipe
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison
from rastro.domain.repair import RepairCandidate
from rastro.ports.recipes import RecipeLifecycle


def repair_candidates(
    candidates: Sequence[RepairCandidate],
    *,
    previous: Recipe,
    key: RecipeKey,
    lifecycle: RecipeLifecycle,
    evaluate: Callable[[RepairCandidate], CandidateEvaluation],
    at: datetime,
) -> tuple[RecipeComparison, ...]:
    """Evaluate all proposals before selecting a unique automatic promotion."""
    evaluated = [(candidate, evaluate(candidate)) for candidate in candidates]
    competing = sum(e.decision == "promote" for _, e in evaluated) > 1
    comparisons = []
    for candidate, evaluation in evaluated:
        recipe = with_version(candidate.recipe, lifecycle.next_version(key))
        if competing and evaluation.decision == "promote":
            evaluation = evaluation.model_copy(
                update={"decision": "quarantine", "reasons": ("competing_candidates",)}
            )
        lifecycle.save_candidate(key, recipe, selected_index=0, at=at)
        if evaluation.decision != "quarantine":
            state = RecipeState.ACTIVE if evaluation.decision == "promote" else RecipeState.RETIRED
            lifecycle.transition(
                key, recipe.version, state, reason="Recipe CI: " + evaluation.decision, at=at
            )
        comparisons.append(RecipeComparison.between(previous, recipe, evaluation))
    return tuple(comparisons)
