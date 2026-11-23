"""Run a recipe's page steps in a Playwright page (click, type, select, scroll, wait, follow).

The vocabulary is closed: each step maps to one fixed Playwright call and recipe values are
only ever passed as arguments (a CSS selector, literal text, an option value), never as code.
Selectors always run in Playwright's CSS engine; a selector that tries to pick another engine
(``xpath=``, ``text=``) or chain one (``>>``) is refused. A followed link is resolved against
the page, checked by the host allowlist before the browser moves, and then still passes the
``NetworkGuard`` routes like any other request. Every call has a timeout and all steps share
one deadline.

The caller arms ``guard`` for the step (``guard.begin``) and reads the resulting document.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import urljoin

from rastro.application.destinations import permitted_url
from rastro.application.page_steps import STEP_TIMEOUT_MS, PageStepError
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.models import Outcome
from rastro.domain.page_steps import (
    ClickStep,
    FollowStep,
    PageStep,
    ScrollStep,
    SelectStep,
    TypeStep,
    WaitStep,
)
from rastro.domain.recipe_steps import STEP_ENGINE

__all__ = ["STEP_TIMEOUT_MS", "perform_steps"]

_SCROLL = "window.scrollTo(0, document.documentElement.scrollHeight)"  # fixed, never recipe data
_HREF = "::attr(href)"


class StepGuard(Protocol):
    """What the steps read from the page's ``NetworkGuard``."""

    policy: AcquisitionPolicy
    requests: int
    issue: str | None


class _Missing(Exception):
    """The step's element did not appear in time."""


class _Clock:
    def __init__(self, timeout_ms: int) -> None:
        self._deadline = asyncio.get_running_loop().time() + timeout_ms / 1000

    def left(self, cap: int = STEP_TIMEOUT_MS) -> int:
        """Milliseconds the next call may wait: its own cap, never past the deadline."""
        remaining = int((self._deadline - asyncio.get_running_loop().time()) * 1000)
        if remaining <= 0:
            raise TimeoutError
        return min(cap, remaining)


async def perform_steps(
    page: Any, steps: Sequence[PageStep], *, guard: StepGuard, timeout_ms: int
) -> None:
    """Run ``steps`` in order on ``page``; raise ``PageStepError`` naming the step that failed."""
    clock = _Clock(timeout_ms)
    for index, step in enumerate(steps, start=1):
        try:
            async with asyncio.timeout(max(clock.left(timeout_ms), 1) / 1000):
                await _perform(page, step, guard, clock)
        except PageStepError:
            raise
        except _Missing:
            raise _error(
                "step_selector_missing", Outcome.INVALID_DATA, index, step, guard
            ) from None
        except AcquisitionError as error:  # a followed link outside what the job allows
            raise _error(error.code, error.outcome, index, step, guard) from None
        except TimeoutError:
            raise _error("step_failed", Outcome.TRANSIENT_FAILURE, index, step, guard) from None
        except Exception:  # a browser error: its text may echo page content, so drop it
            code = guard.issue or "step_failed"
            outcome = Outcome.ACCESS_RESTRICTION if guard.issue else Outcome.TRANSIENT_FAILURE
            raise _error(code, outcome, index, step, guard) from None


def _error(code: str, outcome: Outcome, index: int, step: PageStep, guard: StepGuard) -> Exception:
    return PageStepError(code, outcome, step=index, action=step.action, requests=guard.requests)


def _css(selector: str) -> str:
    """Pin ``selector`` to the CSS engine; refuse engine prefixes and chains."""
    if ">>" in selector or STEP_ENGINE.match(selector):
        raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)
    return f"css={selector}"


async def _find(page: Any, selector: str, clock: _Clock, state: str = "attached") -> None:
    try:
        await page.wait_for_selector(selector, state=state, timeout=clock.left())
    except Exception as error:
        if type(error).__name__ == "TimeoutError":
            raise _Missing from None
        raise


async def _perform(page: Any, step: PageStep, guard: StepGuard, clock: _Clock) -> None:
    if isinstance(step, ScrollStep):
        for _ in range(step.times):
            await page.evaluate(_SCROLL)
            await page.wait_for_timeout(clock.left(step.settle_ms))
        return
    if isinstance(step, WaitStep):
        await _wait(page, step, clock)
        return
    selector = step.selector.removesuffix(_HREF) if isinstance(step, FollowStep) else step.selector
    css = _css(selector)
    await _find(page, css, clock)
    if isinstance(step, ClickStep):
        await page.click(css, timeout=clock.left())
        if step.wait_for is not None:
            await _find(page, _css(step.wait_for), clock, state="visible")
    elif isinstance(step, TypeStep):
        await page.fill(css, step.text, timeout=clock.left())
    elif isinstance(step, SelectStep):
        await page.select_option(css, value=step.value, timeout=clock.left())
    else:
        await _follow(page, css, guard, clock)


async def _wait(page: Any, step: WaitStep, clock: _Clock) -> None:
    if step.selector is None:
        await page.wait_for_timeout(clock.left(step.timeout_ms))
        return
    css = _css(step.selector)
    try:
        await page.wait_for_selector(css, state=step.state, timeout=clock.left(step.timeout_ms))
    except Exception as error:
        appearing = step.state in ("attached", "visible")
        if appearing and type(error).__name__ == "TimeoutError":
            raise _Missing from None
        raise


async def _follow(page: Any, css: str, guard: StepGuard, clock: _Clock) -> None:
    href = await page.get_attribute(css, "href", timeout=clock.left())
    if not href or not href.strip():
        raise _Missing
    url, _, _ = permitted_url(urljoin(page.url, href.strip()), guard.policy)
    cap = guard.policy.request_timeout_seconds * 1000
    await page.goto(url, timeout=clock.left(int(cap)), wait_until="networkidle")
