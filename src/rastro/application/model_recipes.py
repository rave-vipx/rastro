"""Ask any ``ModelPort`` for a recipe, then verify it deterministically (INT-001, REPAIR-001).

Two uses, one loop: a recipe for a new saved page (verified by extracting from that page)
and a repair proposal for a job source (verified by the repair gates through
``propose_job_recipe``). The model sees field names and the page (a bounded digest
when it is large), never records; its output is parsed as a strict proposal and only
kept when verification passes.
Retries are bounded by the ``InferencePolicy``. No provider is special-cased.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from rastro.application.agent_repair import propose_job_recipe
from rastro.application.agent_review import job_repair_context
from rastro.application.proposals import propose_with_model
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import ExtractionResult, Outcome, RastroError, Recipe, Source
from rastro.domain.recipe_compare import RepairReport
from rastro.ports.model import ModelPort

LOCAL_URL = "https://local.invalid/"


@dataclass
class ModelAnswer:
    """The verified recipe (or ``None``), the last failure category and the model calls."""

    recipe: Recipe | None = None
    result: ExtractionResult[Any] | None = None
    reports: list[RepairReport] = field(default_factory=list)
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


async def repair_with_model(
    model: ModelPort,
    path: Path,
    source: int,
    schema: type[BaseModel],
    *,
    policy: InferencePolicy | None = None,
) -> ModelAnswer:
    """Model proposals for a failing job source, each gated and stored like a repair
    candidate; stops at the first that is not rejected."""
    context = await asyncio.to_thread(job_repair_context, path, source)
    answer = ModelAnswer()

    async def evaluate(recipe: Recipe, _: Literal["discovery", "model"]) -> tuple[bool, str]:
        report = await asyncio.to_thread(propose_job_recipe, path, source, recipe)
        answer.reports.append(report)
        evaluation = report.sources[0].comparisons[0].evaluation
        decision = evaluation.decision if evaluation else "reject"
        return decision != "reject", f"recipe_ci_{decision}"

    _, answer.failure, answer.calls = await _ask(
        model,
        outline=context["page_outline"] or "",
        schema=schema,
        failure="recipe_repair",
        evaluate=evaluate,
        policy=policy,
    )
    return answer
