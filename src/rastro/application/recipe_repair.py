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
        recipe, reused = _store(lifecycle, key, candidate.recipe, evaluation, at)
        comparison = RecipeComparison.between(previous, recipe, evaluation)
        comparisons.append(comparison.model_copy(update={"reused": reused}))
    return tuple(comparisons)


def _store(
    lifecycle: RecipeLifecycle,
    key: RecipeKey,
    proposal: Recipe,
    evaluation: CandidateEvaluation,
    at: datetime,
) -> tuple[Recipe, bool]:
    """Store or reuse a version; return it and whether an equal stored version was reused."""
    existing = _equal_stored(lifecycle, key, proposal)
    if existing is not None:
        version, state = existing
        if state == RecipeState.CANDIDATE or evaluation.decision == "reject":
            if state == RecipeState.CANDIDATE:
                _decide(lifecycle, key, version, evaluation, at)
            return with_version(proposal, version), True
        # A retired row cannot be promoted again; a passing proposal gets a fresh version.
    recipe = with_version(proposal, lifecycle.next_version(key))
    lifecycle.save_candidate(key, recipe, selected_index=0, at=at)
    _decide(lifecycle, key, recipe.version, evaluation, at)
    return recipe, False


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
    """The newest equal version, preferring a candidate over a retired twin."""
    get = getattr(lifecycle, "get", None)  # RecipeRepository.get; absent on bare lifecycles
    if not callable(get):
        return None
    matches = []
    for status in sorted(lifecycle.history(key).versions, key=lambda v: -v.version):
        if status.state not in _REUSABLE:
            continue
        stored = get(key, version=status.version)
        if stored is not None and stored.recipe == with_version(proposal, status.version):
            matches.append((status.version, status.state))
    candidates = [m for m in matches if m[1] == RecipeState.CANDIDATE]
    preferred = candidates or matches
    return preferred[0] if preferred else None
