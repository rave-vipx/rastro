"""Replay a recipe's page steps once per source, before its first page is extracted.

Steps are data from the recipe (``CssRecipe.steps``): a closed vocabulary, checked by the
selector cost policy when the recipe is untrusted and by the capability gate
(``page_actions``) for every origin but the operator. They run in the source's open browser
page; the document they leave is what the recipe extracts. A step whose selector no longer
matches is a structural change of the site: the source fails with ``step_selector_missing``
and ``invalid_data``, the same signal drift alerts and repair act on, never an empty success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from rastro.application.capability_gate import CapabilityRefused, require
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import ExtractionResult, Issue, Limits, Outcome, Source
from rastro.domain.page_steps import (
    ClickStep,
    FollowStep,
    PageStep,
    ScrollStep,
    WaitStep,
    steps_capabilities,
)

__all__ = [
    "MAX_STEPS_MS",
    "STEP_TIMEOUT_MS",
    "PageStepError",
    "StepRunningAcquisition",
    "recipe_steps",
    "stepped",
    "steps_budget_ms",
]

STEP_TIMEOUT_MS = 10_000
"""The longest one browser call of a step may wait (finding, clicking, typing)."""
MAX_STEPS_MS = 120_000
"""The longest all steps of one page may take together."""


class PageStepError(AcquisitionError):
    """A step failed; ``step`` is its 1-based position and ``action`` its kind."""

    def __init__(
        self, code: str, outcome: Outcome, *, step: int, action: str, requests: int = 0
    ) -> None:
        super().__init__(code, outcome, requests=requests)
        self.step, self.action = step, action


@runtime_checkable
class StepRunningAcquisition(Protocol):
    """An acquisition port that can run page steps in the page it last rendered."""

    def runs_steps(self, url: str) -> bool:
        """Whether ``url`` renders in a browser page that steps can act on."""
        ...

    async def run_steps(
        self, source: Source, steps: tuple[PageStep, ...], *, max_bytes: int
    ) -> AcquisitionResult:
        """Run ``steps``; raise ``AcquisitionError`` (charged) when they cannot finish."""
        ...


def steps_budget_ms(steps: tuple[PageStep, ...], policy: AcquisitionPolicy) -> int:
    """The time ``steps`` may take together: each step's own bound, at most ``MAX_STEPS_MS``."""
    total = 0.0
    for step in steps:
        if isinstance(step, ScrollStep):
            total += step.times * (step.settle_ms + STEP_TIMEOUT_MS)
        elif isinstance(step, WaitStep):
            total += step.timeout_ms
        elif isinstance(step, FollowStep):
            total += 2 * STEP_TIMEOUT_MS + policy.request_timeout_seconds * 1000
        elif isinstance(step, ClickStep) and step.wait_for is not None:
            total += 3 * STEP_TIMEOUT_MS
        else:
            total += 2 * STEP_TIMEOUT_MS
    return int(min(total, MAX_STEPS_MS))


def recipe_steps(recipe: object) -> tuple[PageStep, ...]:
    """The recipe's page steps; recipes without steps, and no recipe, have none."""
    steps = getattr(recipe, "steps", ())
    return steps if isinstance(steps, tuple) else ()


def stepped[T: BaseModel](
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
    *,
    steps: tuple[PageStep, ...],
    acquisition: object,
    limits: Limits,
) -> Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]]:
    """Wrap one source's page extraction so its first page runs ``steps`` before extraction.

    Later pages of the source (next pages, scroll steps) are extracted as they come.
    """
    if not steps:
        return extract_page
    done = False

    async def run(fetched: AcquisitionResult) -> ExtractionResult[T]:
        nonlocal done
        if done or fetched.status != Outcome.SUCCESS:
            return await extract_page(fetched)
        done = True
        try:
            _granted(steps)  # also for a stored recipe, which the job's own gate never saw
            if not (
                isinstance(acquisition, StepRunningAcquisition)
                and acquisition.runs_steps(fetched.url)
            ):  # an HTTP page has nothing to click: never extract it as if steps had run
                raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)
            after = await acquisition.run_steps(
                Source(url=fetched.url, egress=fetched.egress),
                steps,
                max_bytes=limits.max_input_bytes("html"),
            )
        except AcquisitionError as error:
            failed = replace(fetched, body=None, status=error.outcome, issue_code=error.code)
            return _named(await extract_page(failed), error)
        return await extract_page(after)

    return run


def _granted(steps: tuple[PageStep, ...]) -> None:
    """Refuse steps the current input origin may not run (``page_actions``, ADR 0037)."""
    try:
        require(steps_capabilities(steps))
    except CapabilityRefused:
        raise AcquisitionError("capability_refused", Outcome.ACCESS_RESTRICTION) from None


def _named[T: BaseModel](
    result: ExtractionResult[T], error: AcquisitionError
) -> ExtractionResult[T]:
    """Name the failed step on its issue: position and kind only, never a selector or text."""
    if not isinstance(error, PageStepError):
        return result
    message = f"step {error.step} ({error.action})"
    issues = tuple(
        Issue(code=issue.code, message=message) if issue.code == error.code else issue
        for issue in result.issues
    )
    return result.model_copy(update={"issues": issues})
