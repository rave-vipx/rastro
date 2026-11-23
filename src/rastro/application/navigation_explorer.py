"""Explore a rendered page once with a model and keep the page steps that reveal records.

The model proposes one step at a time from the closed vocabulary (``page_steps``). Each
proposal is data: it is parsed strictly, checked by the selector cost policy and decided by
the capability gate for the ``model`` origin before the trial runs it in a fresh render of
the page, the way a later run replays it. A step is kept only when the replay of all kept
steps shows more records; otherwise it is dropped. Exploration stops at the expected record
count, when the model says stop, or at its step, model-call or time budget. The result is a
recipe whose steps later runs replay without any model; each decision is narrated as an
``ExplorationEvent`` (for example ``clicked 'More': +12 records``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Literal

from pydantic import Field, ValidationError

from rastro.application.capability_gate import model_capabilities
from rastro.application.navigation_prompt import build_navigation_request, parse_navigation_reply
from rastro.domain.capability_grants import CapabilityGrants, decide, default_grants
from rastro.domain.intelligence import InferencePolicy, ModelError
from rastro.domain.models import ConfigurationError, Contract, CssRecipe, Outcome
from rastro.domain.page_outline import clip_untrusted
from rastro.domain.page_steps import MAX_STEPS, FollowStep, PageStep
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.ports.model import ModelPort

__all__ = [
    "ExplorationBudget",
    "ExplorationEvent",
    "ExplorationResult",
    "Observation",
    "StepTrial",
    "explore_navigation",
]

_VERBS = {
    "click": "clicked",
    "type": "typed into",
    "select": "selected",
    "scroll": "scrolled",
    "wait": "waited for",
    "follow": "followed",
}


class ExplorationBudget(Contract):
    """The most steps kept, model calls made and seconds spent on one exploration."""

    max_steps: int = Field(default=5, ge=1, le=MAX_STEPS)
    max_model_calls: int = Field(default=8, ge=1, le=32)
    max_seconds: float = Field(default=180, gt=0, le=900, allow_inf_nan=False)


class ExplorationEvent(Contract):
    """One narrated decision; ``target`` is clipped page text or a selector, never trusted."""

    kind: Literal["observed", "kept", "dropped", "stopped"]
    records: int = Field(ge=0)
    added: int = 0
    action: str | None = None
    target: str | None = None
    reason: str | None = None

    @property
    def message(self) -> str:
        if self.kind == "observed":
            return f"page shows {self.records} records"
        if self.kind == "stopped":
            return f"stopped: {self.reason}"
        verb = _VERBS.get(self.action or "", self.action or "step")
        done = f"{verb} '{self.target}'" if self.target else verb
        return f"{done}: +{self.added} records" if self.kind == "kept" else f"{done}: {self.reason}"


class ExplorationResult(Contract):
    """The recipe with the kept steps, the record counts and every narrated event."""

    recipe: CssRecipe
    records_before: int
    records_after: int
    stop_reason: str
    model_calls: int
    events: tuple[ExplorationEvent, ...]


@dataclass(frozen=True)
class Observation:
    """What a fresh render plus ``steps`` showed: outcome, valid records and the document."""

    status: Outcome
    records: int
    html: str
    issue: str | None = None


type StepTrial = Callable[[tuple[PageStep, ...]], Awaitable[Observation]]
type EventSink = Callable[[ExplorationEvent], None]


async def explore_navigation(
    model: ModelPort,
    *,
    recipe: CssRecipe,
    fields: Sequence[str],
    trial: StepTrial,
    expected_records: int,
    budget: ExplorationBudget | None = None,
    policy: InferencePolicy | None = None,
    grants: CapabilityGrants | None = None,
    on_event: EventSink | None = None,
) -> ExplorationResult:
    """Ask ``model`` for steps, run each through ``trial`` and keep those that add records.

    ``grants`` decides model-origin steps; by default the model may only use page actions.
    """
    budget, policy = budget or ExplorationBudget(), policy or InferencePolicy()
    grants = grants or default_grants(untrusted={"model": frozenset({"page_actions"})})
    events: list[ExplorationEvent] = []

    def emit(event: ExplorationEvent) -> None:
        events.append(event)
        if on_event is not None:
            on_event(event)

    deadline = monotonic() + budget.max_seconds
    seen = await trial(())
    before, kept, calls, failure = seen.records, recipe, 0, "start"
    emit(ExplorationEvent(kind="observed", records=before))
    reason = "expected_records" if before >= expected_records else ""
    while not reason:
        if len(kept.steps) >= budget.max_steps:
            reason = "max_steps"
            break
        if calls >= budget.max_model_calls:
            reason = "model_calls"
            break
        if monotonic() >= deadline:
            reason = "time_budget"
            break
        request = build_navigation_request(
            fields=fields,
            expected=expected_records,
            records=seen.records,
            steps=kept.steps,
            html=seen.html,
            failure=failure,
            policy=policy,
        )
        if request is None:
            reason = "model_context_limit"
            break
        calls += 1
        try:
            async with asyncio.timeout(min(policy.timeout_seconds, deadline - monotonic())):
                output = await model.generate(request, policy)
            step = parse_navigation_reply(output, policy)
        except TimeoutError:
            reason = "model_timeout"
            break
        except ModelError as error:
            if error.code != "invalid_model_response":
                reason = error.code
                break
            failure = "invalid_model_step"
            emit(ExplorationEvent(kind="dropped", records=seen.records, reason=failure))
            continue
        if step is None:
            reason = "model_stopped"
            break
        candidate = _admitted(kept, step, grants)
        if candidate is None:
            failure = "invalid_model_step"
            emit(_step_event("dropped", step, seen, seen, reason=failure))
            continue
        observed = await trial(candidate.steps)
        if observed.status == Outcome.SUCCESS and observed.records > seen.records:
            emit(_step_event("kept", step, seen, observed))
            kept, seen, failure = candidate, observed, "kept"
            if seen.records >= expected_records:
                reason = "expected_records"
        else:
            failure = observed.issue or "no_new_records"
            emit(_step_event("dropped", step, seen, observed, reason=failure))
    emit(ExplorationEvent(kind="stopped", records=seen.records, reason=reason))
    return ExplorationResult(
        recipe=kept,
        records_before=before,
        records_after=seen.records,
        stop_reason=reason,
        model_calls=calls,
        events=tuple(events),
    )


def _admitted(recipe: CssRecipe, step: PageStep, grants: CapabilityGrants) -> CssRecipe | None:
    """The recipe with ``step`` appended, through the same gates as a model recipe, or None."""
    try:
        candidate = CssRecipe.model_validate(
            {**recipe.model_dump(mode="json"), "steps": [*recipe.steps, step]}
        )
        if decide(model_capabilities(candidate), "model", grants).refused:
            return None
        return check_untrusted_recipe(candidate)
    except (ValidationError, ValueError, ConfigurationError):
        return None


def _step_event(
    kind: Literal["kept", "dropped"],
    step: PageStep,
    before: Observation,
    after: Observation,
    *,
    reason: str | None = None,
) -> ExplorationEvent:
    return ExplorationEvent(
        kind=kind,
        records=after.records,
        added=max(0, after.records - before.records),
        action=step.action,
        target=_target(before.html, step),
        reason=reason,
    )


def _target(html: str, step: PageStep) -> str | None:
    """The visible text of the step's element in the page before it, else its selector."""
    selector = getattr(step, "selector", None)
    if not isinstance(selector, str):
        return None
    if isinstance(step, FollowStep):
        selector = selector.removesuffix("::attr(href)")
    text = ""
    try:
        from rastro.adapters.scrapling import ScraplingParser
        from rastro.domain.models import Limits

        probe = CssRecipe(record_selector=selector, fields={"label": "::text"})
        found = ScraplingParser().parse(html, probe, Limits()).candidates
        text = str(found[0].values.get("label") or "") if found else ""
    except (ValidationError, ValueError, ConfigurationError):
        text = ""
    return clip_untrusted(text or selector, 40) or None
