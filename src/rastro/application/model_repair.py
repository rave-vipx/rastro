"""Adaptive candidates, then model fallback and semantic gate for repair (REPAIR-001/002).

Relocation, fallbacks and discovery run first. When none of their candidates would pass
every gate, the engine's adaptive tools propose recipes anchored on the retained values
(``application/adaptive_candidates``); one that passes recipe CI and the strict value
checks is kept and no model is called. Only when these fail too does the model propose
recipes, within the policy's call budget; each proposal is gated by the same evaluator
as a deterministic one plus the deterministic value checks (types, shape, values
borrowed from another field's history), and a rejected proposal triggers a retry
carrying only a failure category. Every relocated or proposed candidate that would
still promote then meets the semantic judge, which can veto but never promote on its
own. Without a model only the deterministic and adaptive stages run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from pydantic import BaseModel, JsonValue

from rastro.application.adaptive_candidates import (
    adaptive_repair,
    passing,
)
from rastro.application.adaptive_candidates import with_history as with_history
from rastro.application.proposals import propose_with_model, schema_hints
from rastro.application.repair_checks import _candidate as _candidate
from rastro.application.repair_checks import check_values as check_values
from rastro.application.semantic_judge import apply_verdicts, judge, judge_request
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Issue,
    Limits,
    Recipe,
    RecipeAttempt,
)
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison
from rastro.domain.repair import RepairCandidate
from rastro.ports.engine import AdaptiveLocator
from rastro.ports.model import ModelPort

type Evaluated = list[tuple[RepairCandidate, CandidateEvaluation]]
type Rows = list[dict[str, JsonValue]]
type Origin = Literal["relocation", "adaptive", "model"]


@dataclass(frozen=True)
class ModelAssist:
    """A configured model and its budgets; ``None`` in its place keeps repair deterministic."""

    model: ModelPort
    policy: InferencePolicy


@dataclass(frozen=True)
class AssistedRepair:
    evaluated: Evaluated
    origin: Origin | None
    model_calls: int
    issues: tuple[Issue, ...]


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
    adaptive: AdaptiveLocator | None = None,
    limits: Limits | None = None,
    timezone: str | None = None,
) -> AssistedRepair:
    """Try adaptive candidates, then gated model proposals; judge every would-be promotion.

    ``adaptive`` defaults to the engine's locator. A passing adaptive candidate ends
    repair without any model call; failing ones are dropped, not stored. ``timezone`` is the
    source's region timezone, given to the model so local dates parse as the site means them.
    """
    origin: Origin | None = "relocation" if evaluated else None
    evaluated = list(evaluated)

    def strict_gate(candidate: RepairCandidate) -> CandidateEvaluation:
        evaluation = gate(candidate)
        return check_values(evaluation, candidate.recipe, schema, previous, rows, history)

    if isinstance(previous, CssRecipe) and not any(passing(e) for _, e in evaluated):
        normalize = partial(_candidate, schema=schema, previous=previous, origin="adaptive")
        rescued = await adaptive_repair(
            adaptive, html, previous, history, limits or Limits(), normalize, strict_gate
        )
        if rescued:
            return AssistedRepair([*evaluated, *rescued], "adaptive", 0, ())
    if assist is None:
        return AssistedRepair(evaluated, origin, 0, ())
    calls = 0
    issues: tuple[Issue, ...] = ()
    if previous.representation == "html" and not any(passing(e) for _, e in evaluated):
        failure = (
            "serving_recipe_extracts_no_records"
            if not await asyncio.to_thread(rows, previous)
            else "serving_recipe_fails_gates"
        )
        proposed, calls, issues = await _propose(
            assist,
            html,
            schema,
            previous,
            strict_gate,
            samples=history,
            failure=failure,
            timezone=timezone,
        )
        if proposed:
            origin = "model"
            evaluated.extend(proposed)
    judged: Evaluated = []
    for candidate, evaluation in evaluated:
        if evaluation.decision == "promote":  # any origin: the judge can only veto
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
    timezone: str | None = None,
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
        timezone=timezone,
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
