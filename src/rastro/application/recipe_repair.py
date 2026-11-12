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

_REUSABLE = (RecipeState.CANDIDATE, RecipeState.RETIRED)


def repair_candidates(
    candidates: Sequence[RepairCandidate],
    *,
    previous: Recipe,
    key: RecipeKey,
    lifecycle: RecipeLifecycle,
    evaluate: Callable[[RepairCandidate], CandidateEvaluation],
    at: datetime,
) -> tuple[RecipeComparison, ...]:
    """Evaluate all proposals before selecting a unique automatic promotion.

    A proposal equal to a stored candidate reuses that version, and one equal to a
    retired version that is rejected again reuses it too, so repeated repair runs do
    not allocate new versions for the same definition.
    """
    evaluated = [(candidate, evaluate(candidate)) for candidate in candidates]
    competing = sum(e.decision == "promote" for _, e in evaluated) > 1
    comparisons = []
    for candidate, evaluation in evaluated:
        if competing and evaluation.decision == "promote":
            evaluation = evaluation.model_copy(
                update={"decision": "quarantine", "reasons": ("competing_candidates",)}
            )
        recipe = _store(lifecycle, key, candidate.recipe, evaluation, at)
        comparisons.append(RecipeComparison.between(previous, recipe, evaluation))
    return tuple(comparisons)


def _store(
    lifecycle: RecipeLifecycle,
    key: RecipeKey,
    proposal: Recipe,
    evaluation: CandidateEvaluation,
    at: datetime,
) -> Recipe:
    existing = _equal_stored(lifecycle, key, proposal)
    if existing is not None:
        version, state = existing
        if state == RecipeState.RETIRED and evaluation.decision != "reject":
            existing = None  # a retired row cannot be promoted again; store a fresh one
        else:
            recipe = with_version(proposal, version)
            if state == RecipeState.CANDIDATE:
                _decide(lifecycle, key, version, evaluation, at)
            return recipe
    recipe = with_version(proposal, lifecycle.next_version(key))
    lifecycle.save_candidate(key, recipe, selected_index=0, at=at)
    _decide(lifecycle, key, recipe.version, evaluation, at)
    return recipe


def _decide(
    lifecycle: RecipeLifecycle,
    key: RecipeKey,
    version: int,
    evaluation: CandidateEvaluation,
    at: datetime,
) -> None:
    """Promote activates, reject retires (with a lifecycle event), quarantine stays."""
    if evaluation.decision == "quarantine":
        return
    state = RecipeState.ACTIVE if evaluation.decision == "promote" else RecipeState.RETIRED
    lifecycle.transition(key, version, state, reason="Recipe CI: " + evaluation.decision, at=at)


def _equal_stored(
    lifecycle: RecipeLifecycle, key: RecipeKey, proposal: Recipe
) -> tuple[int, RecipeState] | None:
    get = getattr(lifecycle, "get", None)  # RecipeRepository.get; absent on bare lifecycles
    if not callable(get):
        return None
    for status in lifecycle.history(key).versions:
        if status.state not in _REUSABLE:
            continue
        stored = get(key, version=status.version)
        if stored is not None and stored.recipe == with_version(proposal, status.version):
            return status.version, status.state
    return None
