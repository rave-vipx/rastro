"""Value gate and candidate reuse on top of recipe CI (REPAIR-002, REPAIR-004, REC-006).

``gate_values`` downgrades a passing evaluation to quarantine when a relocated
field's values no longer look like that field's retained values. Job repair can
reuse it; it is not wired there yet. ``store_candidates`` reuses a stored
candidate with an identical definition instead of creating a new version.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, JsonValue

from rastro.application.extraction import extract_document, validate_recipe
from rastro.application.intelligence import with_version
from rastro.application.recipe_repair import repair_candidates
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey, StoredRecipe
from rastro.domain.models import Limits, Recipe, Source
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison
from rastro.domain.repair import RepairCandidate
from rastro.domain.value_consistency import value_consistency
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository

_REUSE_WINDOW = 32  # newest versions scanned for an identical stored candidate


class CandidateStore(RecipeRepository, RecipeLifecycle, Protocol):
    """A recipe store that also controls lifecycle states."""


def gate_values(
    evaluation: CandidateEvaluation,
    candidate: RepairCandidate,
    *,
    previous: Recipe,
    document: str,
    source: Source,
    schema: type[BaseModel],
    parser: DocumentParser,
    limits: Limits,
    history: Sequence[Mapping[str, JsonValue]],
) -> CandidateEvaluation:
    """Quarantine a non-rejected candidate whose relocated values changed shape."""
    if evaluation.decision == "reject":
        return evaluation
    relocated = RecipeComparison.between(previous, candidate.recipe).changed_fields
    recipe = validate_recipe(schema, candidate.recipe, candidate.recipe.representation)
    current = extract_document(
        document, source=source, schema=schema, recipe=recipe, parser=parser, limits=limits
    )
    rows = [record.data.model_dump(mode="json") for record in current.records]
    failed = value_consistency(rows, history, relocated)
    if not failed:
        return evaluation
    kept = tuple(r for r in evaluation.reasons if r != "all_gates_passed")
    return evaluation.model_copy(update={"decision": "quarantine", "reasons": (*kept, *failed)})


def store_candidates(
    evaluated: Sequence[tuple[RepairCandidate, CandidateEvaluation]],
    *,
    previous: Recipe,
    key: RecipeKey,
    lifecycle: CandidateStore,
    at: datetime,
) -> tuple[RecipeComparison, ...]:
    """Persist new candidates; re-decide identical stored ones under their own version."""
    if sum(e.decision == "promote" for _, e in evaluated) > 1:
        competing = {"decision": "quarantine", "reasons": ("competing_candidates",)}
        evaluated = [
            (c, e.model_copy(update=competing) if e.decision == "promote" else e)
            for c, e in evaluated
        ]
    stored = _stored_candidates(lifecycle, key)
    comparisons: list[RecipeComparison] = []
    fresh: dict[int, CandidateEvaluation] = {}
    for candidate, evaluation in evaluated:
        match = next(
            (s for s in stored if with_version(candidate.recipe, s.recipe.version) == s.recipe),
            None,
        )
        if match is None:
            fresh[id(candidate)] = evaluation
            continue
        if evaluation.decision != "quarantine":
            state = RecipeState.ACTIVE if evaluation.decision == "promote" else RecipeState.RETIRED
            lifecycle.transition(
                key, match.recipe.version, state, reason="Recipe CI: " + evaluation.decision, at=at
            )
        comparisons.append(RecipeComparison.between(previous, match.recipe, evaluation))
    new = [c for c, _ in evaluated if id(c) in fresh]
    comparisons.extend(
        repair_candidates(
            new,
            previous=previous,
            key=key,
            lifecycle=lifecycle,
            evaluate=lambda c: fresh[id(c)],
            at=at,
        )
    )
    return tuple(comparisons)


def _stored_candidates(lifecycle: CandidateStore, key: RecipeKey) -> list[StoredRecipe]:
    newest = lifecycle.next_version(key) - 1
    found = []
    for version in range(newest, max(newest - _REUSE_WINDOW, 0), -1):
        item = lifecycle.get(key, version=version)
        if item is not None and item.metadata.state == RecipeState.CANDIDATE:
            found.append(item)
    return found
