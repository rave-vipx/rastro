"""Climb the acquisition ladder when a site blocks a source (ADR 0039).

A blocked source is tried again one level up (see ``ladder_levels``) until a level returns
the page. Each level passes the capability gate for the job's origin first; a refused level
is recorded as ``not_granted`` and skipped. The lowest level that worked is remembered per
recipe in recipe memory, so later runs start there and climb only on a new block; every
``PROBE_EVERY`` runs a run starts again at the job's floor to notice a lifted block. Every
level tried and how it ended goes into the source's acquisition notes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from typing import Literal

from rastro.application.capability_gate import CapabilityRefused, policy_capabilities, require
from rastro.application.ladder_levels import LEVELS, Rung
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import ConfigurationError, Outcome
from rastro.domain.source_notes import LadderLevel, LadderStep

__all__ = ["StepOutcome", "climb", "climb_async", "fetch_outcome", "gate"]

type StepOutcome = Literal["ok", "blocked", "failed"]


def gate(rung: Rung) -> bool:
    """Whether the job's origin may use ``rung``; solver plugins are grant-only."""
    requested = policy_capabilities(rung.policy)
    if rung.level == "solver_plugin":
        requested = frozenset({"solver_plugins"})
    try:
        require(requested)
    except CapabilityRefused:
        return False
    return True


def fetch_outcome(fetched: AcquisitionResult) -> StepOutcome:
    """A refused fetch climbs, except for robots.txt, which no level may get around."""
    robots = fetched.issue_code == "robots_denied" or (
        fetched.blocked is not None and fetched.blocked.kind == "robots"
    )
    if (fetched.status == Outcome.ACCESS_RESTRICTION or fetched.blocked) and not robots:
        return "blocked"
    ok = (Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS, Outcome.UNCHANGED)
    return "ok" if fetched.status in ok else "failed"


def _from(rungs: Sequence[Rung], start: LadderLevel) -> Iterator[Rung]:
    first = LEVELS.index(start)
    return (rung for rung in rungs if LEVELS.index(rung.level) >= first)


def climb[R](
    rungs: Sequence[Rung],
    start: LadderLevel,
    attempt: Callable[[Rung], tuple[R, StepOutcome]],
    granted: Callable[[Rung], bool],
) -> tuple[R | None, tuple[LadderStep, ...], Rung | None]:
    """Try ``rungs`` from ``start`` up; stop at the first that is not blocked.

    Returns the last attempt's value, every step, and the rung that worked (None when every
    level was blocked, refused or could not be built, or when a level failed without a
    block, which climbing does not fix). A level that cannot be built, such as a browser
    without its extra, is recorded as ``failed`` and the climb goes on.
    """
    value: R | None = None
    steps: list[LadderStep] = []
    for rung in _from(rungs, start):
        if not granted(rung):
            steps.append(LadderStep(level=rung.level, outcome="not_granted"))
            continue
        try:
            value, outcome = attempt(rung)
        except ConfigurationError:
            steps.append(LadderStep(level=rung.level, outcome="failed"))
            continue
        steps.append(LadderStep(level=rung.level, outcome=outcome))
        if outcome == "ok":
            return value, tuple(steps), rung
        if outcome == "failed":
            break
    return value, tuple(steps), None


async def climb_async[R](
    rungs: Sequence[Rung],
    start: LadderLevel,
    attempt: Callable[[Rung], Awaitable[tuple[R, StepOutcome]]],
    granted: Callable[[Rung], bool],
) -> tuple[R | None, tuple[LadderStep, ...], Rung | None]:
    """``climb`` for an asynchronous ``attempt``; the same steps and the same stops."""
    value: R | None = None
    steps: list[LadderStep] = []
    for rung in _from(rungs, start):
        if not granted(rung):
            steps.append(LadderStep(level=rung.level, outcome="not_granted"))
            continue
        try:
            value, outcome = await attempt(rung)
        except ConfigurationError:
            steps.append(LadderStep(level=rung.level, outcome="failed"))
            continue
        steps.append(LadderStep(level=rung.level, outcome=outcome))
        if outcome == "ok":
            return value, tuple(steps), rung
        if outcome == "failed":
            break
    return value, tuple(steps), None
