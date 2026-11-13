"""Strict proposal decoding and bounded inference retries (AI-001-004, REPAIR-001).

A page too large for the context budget is replaced by its digest in the prompt; the
candidate is still executed and validated against the full original document.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ValidationError

from rastro.application.page_digest import digest_page
from rastro.application.proposal_prompt import build_proposal_request
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, RecipeProposal
from rastro.domain.json import strict_json
from rastro.domain.models import (
    ConfigurationError,
    Limits,
    Outcome,
    RastroError,
    Recipe,
    RecipeAttempt,
)
from rastro.ports.model import ModelPort

type CandidateEvaluator = Callable[
    [Recipe, Literal["discovery", "model"]], Awaitable[tuple[bool, str]]
]


def parse_proposal(output: str, policy: InferencePolicy) -> Recipe:
    if len(output.encode()) > policy.max_response_bytes:
        raise ModelError("model_response_limit")
    return RecipeProposal.model_validate(strict_json(output), strict=True).recipe()


async def propose_with_model(
    *,
    model: ModelPort,
    policy: InferencePolicy,
    slots: asyncio.Semaphore,
    html: str,
    schema: type[BaseModel],
    failure: str,
    attempts: list[RecipeAttempt],
    evaluate: CandidateEvaluator,
    recipe: Recipe | None = None,
    samples: Sequence[Mapping[str, object]] = (),
    limits: Limits | None = None,
) -> tuple[bool, str, int, float]:
    calls = 0
    complete = False
    inference_start = perf_counter()
    page: dict[str, str | None] = {"html": html, "digest": None}
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            for _ in range(min(policy.max_calls, 1 + policy.max_repair_attempts)):
                request = await _fitting_request(
                    page,
                    policy,
                    limits,
                    schema=schema,
                    failure=failure,
                    recipe=recipe,
                    samples=samples,
                )
                if request is None:
                    failure = "model_context_limit"
                    break
                async with slots:
                    calls += 1
                    output = await model.generate(request, policy)
                try:
                    candidate = parse_proposal(output, policy)
                    complete, failure = await evaluate(candidate, "model")
                    if complete:
                        break
                except (ValidationError, ValueError, RecursionError, ConfigurationError):
                    failure = "invalid_model_candidate"
                    attempts.append(
                        RecipeAttempt(
                            index=len(attempts),
                            origin="model",
                            status=Outcome.INSUFFICIENT_EVIDENCE,
                        )
                    )
    except TimeoutError:
        failure = "model_timeout"
    except ModelError as error:
        failure = error.code
    return complete, failure, calls, perf_counter() - inference_start


async def _fitting_request(
    page: dict[str, str | None],
    policy: InferencePolicy,
    limits: Limits | None,
    **parts: object,
) -> ModelRequest | None:
    """The raw page if it fits the context budget, else its digest; None if neither fits."""
    request = build_proposal_request(html=page["html"], digest=page["digest"], **parts)  # type: ignore[arg-type]
    if request.context_bytes <= policy.max_context_bytes or page["digest"] is not None:
        return request if request.context_bytes <= policy.max_context_bytes else None
    try:
        page["digest"] = await asyncio.to_thread(
            digest_page, page["html"] or "", limits=limits or Limits()
        )
    except RastroError:
        return None
    request = build_proposal_request(digest=page["digest"], **parts)  # type: ignore[arg-type]
    return request if request.context_bytes <= policy.max_context_bytes else None
