"""Model fallback and semantic gate for repair, after deterministic relocation (REPAIR-001/002).

Relocation, fallbacks and discovery run first. Only when none of their candidates would
pass every gate does the model propose recipes, within the policy's call budget; each
proposal is gated by the same evaluator as a deterministic one, and a rejected proposal
triggers a retry carrying only a failure category. Before any automatic promotion, the
semantic judge checks that the changed fields still carry their meaning.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from rastro.application.extraction import validate_recipe
from rastro.application.proposals import propose_with_model, schema_hints
from rastro.application.semantic_judge import apply_verdicts, judge, judge_request
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import ConfigurationError, CssRecipe, Issue, Recipe, RecipeAttempt
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison
from rastro.domain.repair import RepairCandidate
from rastro.ports.model import ModelPort

type Evaluated = list[tuple[RepairCandidate, CandidateEvaluation]]
type Rows = list[dict[str, JsonValue]]
_PASSING_REASONS = {"all_gates_passed", "automatic_promotion_disabled"}


@dataclass(frozen=True)
class ModelAssist:
    """A configured model and its budgets; ``None`` in its place keeps repair deterministic."""

    model: ModelPort
    policy: InferencePolicy


@dataclass(frozen=True)
class AssistedRepair:
    evaluated: Evaluated
    origin: Literal["relocation", "model"] | None
    model_calls: int
    issues: tuple[Issue, ...]


def passing(evaluation: CandidateEvaluation) -> bool:
    """Would promote if promotion were enabled: no rejection and no quarantine gate failed."""
    return evaluation.decision != "reject" and set(evaluation.reasons) <= _PASSING_REASONS


async def assist_repair(
    assist: ModelAssist | None,
    evaluated: Evaluated,
    *,
    html: str,
    schema: type[BaseModel],
    previous: Recipe,
    gate: Callable[[RepairCandidate], CandidateEvaluation],
    rows: Callable[[Recipe], Rows],
    history: Sequence[Mapping[str, JsonValue]],
) -> AssistedRepair:
    """Add gated model proposals when needed, then judge every would-be promotion."""
    origin: Literal["relocation", "model"] | None = "relocation" if evaluated else None
    if assist is None:
        return AssistedRepair(evaluated, origin, 0, ())
    evaluated = list(evaluated)
    calls = 0
    issues: tuple[Issue, ...] = ()
    if previous.representation == "html" and not any(passing(e) for _, e in evaluated):
        failure = (
            "serving_recipe_extracts_no_records"
            if not await asyncio.to_thread(rows, previous)
            else "serving_recipe_fails_gates"
        )
        proposed, calls, issues = await _propose(
            assist, html, schema, previous, gate, samples=history, failure=failure
        )
        if proposed:
            origin = "model"
            evaluated.extend(proposed)
    judged: Evaluated = []
    for candidate, evaluation in evaluated:
        if evaluation.decision == "promote":
            calls += 1
            evaluation = await _judge(
                assist, schema, previous, candidate, rows, history, evaluation
            )
        judged.append((candidate, evaluation))
    return AssistedRepair(judged, origin, calls, issues)


async def _propose(
    assist: ModelAssist,
    html: str,
    schema: type[BaseModel],
    previous: Recipe,
    gate: Callable[[RepairCandidate], CandidateEvaluation],
    *,
    samples: Sequence[Mapping[str, JsonValue]],
    failure: str,
) -> tuple[Evaluated, int, tuple[Issue, ...]]:
    proposed: Evaluated = []

    async def evaluate(proposal: Recipe, _: Literal["discovery", "model"]) -> tuple[bool, str]:
        """Gate the history-compatible union first, then the plain proposal; keep one."""
        plain = _candidate(proposal, schema, previous)
        if plain is None:
            return False, "unchanged_recipe"
        try:
            union = _candidate(with_history(plain.recipe, previous), schema, previous)
        except ConfigurationError:
            union = None
        options = [plain] if union is None or union.recipe == plain.recipe else [union, plain]
        fresh = [c for c in options if all(c.recipe != e.recipe for e, _ in proposed)]
        if not fresh:
            return False, "repeated_candidate"
        first: tuple[RepairCandidate, CandidateEvaluation] | None = None
        for candidate in fresh:
            try:
                evaluation = await asyncio.to_thread(gate, candidate)
            except ConfigurationError:  # e.g. an invalid selector: this option is out
                continue
            first = first or (candidate, evaluation)
            if passing(evaluation):
                proposed.append((candidate, evaluation))
                return True, "candidate_collected"
        if first is None:  # counted by the proposal loop as invalid_model_candidate
            raise ConfigurationError("Model repair candidate cannot be evaluated")
        proposed.append(first)
        return False, "candidate_failed_recipe_ci"

    attempts: list[RecipeAttempt] = []
    complete, failure, calls, _ = await propose_with_model(
        model=assist.model,
        policy=assist.policy,
        slots=asyncio.Semaphore(assist.policy.max_concurrency),
        html=html,
        fields=tuple(schema.model_fields),
        required={name for name, field in schema.model_fields.items() if field.is_required()},
        failure=failure,
        attempts=attempts,
        evaluate=evaluate,
        hints=schema_hints(schema),
        previous=previous,
        samples=samples,
    )
    return proposed, calls, () if complete else (Issue(code=failure),)


async def _judge(
    assist: ModelAssist,
    schema: type[BaseModel],
    previous: Recipe,
    candidate: RepairCandidate,
    rows: Callable[[Recipe], Rows],
    history: Sequence[Mapping[str, JsonValue]],
    evaluation: CandidateEvaluation,
) -> CandidateEvaluation:
    """Judge the changed fields, or every field when only the record container moved."""
    changed = RecipeComparison.between(previous, candidate.recipe).changed_fields
    fields = changed or tuple(schema.model_fields)
    current = await asyncio.to_thread(rows, candidate.recipe)
    request = judge_request(schema, fields, current, history)
    return apply_verdicts(evaluation, await judge(assist.model, assist.policy, request, fields))


def with_history(proposal: Recipe, previous: Recipe) -> Recipe:
    """Prefix each changed CSS locator with the serving one, as relocation does.

    ``"old, new"`` keeps historical pages matching, so fixture replay can pass; any
    union that does not validate falls back to the plain proposal.
    """
    if not (isinstance(proposal, CssRecipe) and isinstance(previous, CssRecipe)):
        return proposal
    fields = {name: _union(previous.fields.get(name), sel) for name, sel in proposal.fields.items()}
    record = _union(previous.record_selector, proposal.record_selector)
    try:
        return Recipe.model_validate(
            {**proposal.model_dump(), "fields": fields, "record_selector": record}
        )
    except ValidationError:
        return proposal


def _union(old: str | None, new: str) -> str:
    if old is None or old == new or new.startswith(old + ","):
        return new
    return f"{old}, {new}"


def _candidate(
    proposal: Recipe, schema: type[BaseModel], previous: Recipe
) -> RepairCandidate | None:
    data = proposal.model_dump()
    data.update(require_evidence=True, fallbacks=(), version=previous.version)
    try:
        recipe = validate_recipe(schema, Recipe.model_validate(data), previous.representation)
    except ValidationError:
        raise ConfigurationError("Invalid model repair candidate") from None
    baseline = previous.model_copy(update={"require_evidence": True, "fallbacks": ()})
    if recipe == baseline:
        return None
    return RepairCandidate(
        recipe=recipe, origin="model", rationale="Model proposal; requires recipe CI"
    )
