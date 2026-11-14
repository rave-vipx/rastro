"""Model-proposed CSS recipes for ``suggest``, verified like example-built ones (INT-002, AI-001).

The model sees the page, the field names, types and descriptions, and proposes one
recipe per call within the policy's budget. Each proposal must reproduce every example
(when given), extract at least ``records_min`` records and pass the schema; a rejected
proposal triggers a retry carrying only a failure category. The model never supplies values.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from rastro.application.proposals import propose_with_model, schema_hints
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import ConfigurationError, CssRecipe, Outcome, Recipe, RecipeAttempt
from rastro.domain.suggest import FieldConfidence, Suggestion, SuggestionError
from rastro.ports.model import ModelPort
from rastro.ports.suggest import RecipeCandidate

type Verify = Callable[[Sequence[RecipeCandidate]], Suggestion[Any]]


def suggest_with_model(
    model: ModelPort,
    html: str,
    schema: type[BaseModel],
    *,
    verify: Verify,
    records_min: int,
    policy: InferencePolicy | None = None,
) -> Suggestion[Any]:
    """Run the bounded proposal loop; raise ``SuggestionError`` when nothing verifies."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ConfigurationError("suggest with a model cannot run inside a running event loop")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_propose(model, html, schema, verify, records_min, policy))


async def _propose(
    model: ModelPort,
    html: str,
    schema: type[BaseModel],
    verify: Verify,
    records_min: int,
    policy: InferencePolicy | None,
) -> Suggestion[Any]:
    found: list[Suggestion[Any]] = []

    async def evaluate(proposal: Recipe, _: Literal["discovery", "model"]) -> tuple[bool, str]:
        if not isinstance(proposal, CssRecipe):
            return False, "css_recipe_required"
        notes = {
            name: FieldConfidence(field=name, method="model", selector=proposal.fields.get(name))
            for name in schema.model_fields
        }
        try:
            suggestion = await asyncio.to_thread(verify, (RecipeCandidate(proposal, notes),))
        except SuggestionError:
            return False, "examples_not_reproduced"
        if len(suggestion.preview.records) < records_min:
            return False, "too_few_records"
        found.append(suggestion)
        return suggestion.status is Outcome.SUCCESS, "partial_records"

    policy = policy or InferencePolicy()
    attempts: list[RecipeAttempt] = []
    _, failure, _, _ = await propose_with_model(
        model=model,
        policy=policy,
        slots=asyncio.Semaphore(policy.max_concurrency),
        html=html,
        fields=tuple(schema.model_fields),
        required={name for name, info in schema.model_fields.items() if info.is_required()},
        failure="suggest_recipe",
        attempts=attempts,
        evaluate=evaluate,
        hints=schema_hints(schema),
    )
    best = next((s for s in found if s.status is Outcome.SUCCESS), found[0] if found else None)
    if best is None:
        raise SuggestionError(f"The model proposed no recipe that verified ({failure})")
    return best
