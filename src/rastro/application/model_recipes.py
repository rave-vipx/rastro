"""Ask any ``ModelPort`` for a recipe for a saved page, then verify it (INT-001).

The recipe is kept only if it extracts valid records from that page (repair proposals go
through ``repair_job_source(..., model=...)`` instead). The model sees field names and the
page (a bounded digest when it is large), never records; its output is parsed as a strict
proposal and only kept when verification passes.
Retries are bounded by the ``InferencePolicy``. No provider is special-cased.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from rastro.application.proposals import propose_with_model
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import ExtractionResult, Outcome, RastroError, Recipe, Source
from rastro.ports.model import ModelPort

LOCAL_URL = "https://local.invalid/"


@dataclass
class ModelAnswer:
    """The verified recipe (or ``None``), the last failure category and the model calls."""

    recipe: Recipe | None = None
    result: ExtractionResult[Any] | None = None
    failure: str = ""
    calls: int = 0


async def _ask(
    model: ModelPort,
    *,
    outline: str,
    schema: type[BaseModel],
    failure: str,
    evaluate: Any,
    policy: InferencePolicy | None,
) -> tuple[bool, str, int]:
    complete, failure, calls, _ = await propose_with_model(
        model=model,
        policy=policy or InferencePolicy(),
        slots=asyncio.Semaphore(1),
        html=outline,
        fields=tuple(schema.model_fields),
        required={n for n, f in schema.model_fields.items() if f.is_required()},
        failure=failure,
        attempts=[],
        evaluate=evaluate,
    )
    return complete, failure, calls


async def recipe_for_page(
    model: ModelPort,
    html: str,
    schema: type[BaseModel],
    *,
    url: str | None = None,
    policy: InferencePolicy | None = None,
) -> ModelAnswer:
    """A model-proposed CSS recipe kept only if it extracts valid records from ``html``."""
    from rastro.application.engine import Rastro

    answer = ModelAnswer()
    engine = Rastro()

    async def evaluate(recipe: Recipe, _: Literal["discovery", "model"]) -> tuple[bool, str]:
        candidate = recipe.model_copy(update={"require_evidence": True})
        try:
            result = await asyncio.to_thread(
                engine.extract_html,
                html,
                source=Source(url=url or LOCAL_URL),
                schema=schema,
                recipe=candidate,
            )
        except RastroError:
            return False, "invalid_model_candidate"
        if result.status != Outcome.SUCCESS or not result.records:
            return False, str(result.status)
        answer.recipe, answer.result = candidate, result
        return True, "verified"

    complete, answer.failure, answer.calls = await _ask(
        model,
        outline=html,  # digested by the proposal loop when it is too large
        schema=schema,
        failure="new_recipe",
        evaluate=evaluate,
        policy=policy,
    )
    if not complete:
        answer.recipe = answer.result = None
    return answer
