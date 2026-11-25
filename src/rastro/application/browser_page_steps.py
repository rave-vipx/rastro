"""Page steps in a source's open browser page, under the shared budget, pacing and policy.

The steps act on the page the session last rendered for the source. They get only the
requests the source's budget has left, one deadline for all of them, and the operator's
network grants, never more. A document they end on that is another page (a followed link, a
click that navigated) must still be an allowed URL and pass robots before it is extracted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import replace
from time import perf_counter

from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    _Host,
    charge_extra_requests,
    check_page_request,
)
from rastro.application.browser_policy import canonical_url, page_policy
from rastro.application.browser_sessions import SOURCE_SESSIONS
from rastro.application.destinations import permitted_url
from rastro.application.operator_trust import current_trust
from rastro.application.page_steps import steps_budget_ms
from rastro.application.scroll import refund_page, spend_page
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import Outcome, Source
from rastro.domain.page_steps import PageStep, ScrollStep
from rastro.ports.browser import BrowserSessions
from rastro.ports.engine import PageStepRunner

Robots = Callable[[str], Awaitable[None]]


async def run_page_steps(
    renderer: object,
    base: AcquisitionPolicy,
    hosts: MutableMapping[str, _Host],
    slots: asyncio.Semaphore,
    source: Source,
    steps: tuple[PageStep, ...],
    *,
    max_bytes: int,
    robots: Robots,
) -> AcquisitionResult:
    """Run ``steps`` on the source's page; raise a charged ``AcquisitionError`` on failure."""
    scope = SOURCE_SESSIONS.get()
    policy = base.with_grants(current_trust().network_grants())
    canonical, hostname, _ = permitted_url(source.url, policy)
    policy = page_policy(policy, canonical)
    session = (
        scope.showing(renderer, host=hostname, url=canonical)
        if scope is not None and isinstance(renderer, BrowserSessions)
        else None
    )
    if scope is None or not isinstance(session, PageStepRunner):  # nothing to act on
        raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)
    _spend_scroll_pages(steps)
    host, started, granted = hosts[hostname], perf_counter(), 0
    async with host.lock, slots:
        await asyncio.sleep(max(0, host.next_request - perf_counter()))
        try:
            check_page_request(source.url)
            budget = PAGINATION_SCOPE.get()
            granted = budget.remaining + 1 if budget else policy.max_requests
            step_policy = policy.model_copy(update={"max_requests": granted})
            deadline = steps_budget_ms(steps, policy) / 1000 + policy.request_timeout_seconds
            async with asyncio.timeout(deadline):
                result = await session.run_steps(steps, policy=step_policy, max_bytes=max_bytes)
        except AcquisitionError as error:
            await scope.discard(renderer)  # the page is in an unknown state now
            charge_extra_requests(max(0, error.requests - 1))
            raise
        except TimeoutError:  # the browser's count died with the steps: charge all of it
            await scope.discard(renderer)
            charge_extra_requests(max(0, granted - 1))
            raise AcquisitionError(
                "step_failed", Outcome.TRANSIENT_FAILURE, requests=granted
            ) from None
        finally:
            host.next_request = perf_counter() + host.interval
    charge_extra_requests(max(0, result.requests - 1))
    try:
        await _check_document(result, policy, canonical, max_bytes, robots)
    except AcquisitionError:
        await scope.discard(renderer)  # the page shows what the job may not use
        raise
    if not await scope.leave_foreign(renderer, result.url):  # another host: never reused
        scope.mark_rendered(renderer, canonical_url(result.url, policy))
    return replace(result, acquisition_seconds=perf_counter() - started)


def _spend_scroll_pages(steps: tuple[PageStep, ...]) -> None:
    """A scroll step spends the crawl's page cap like a scroll of the scroll setting does."""
    wanted = sum(step.times for step in steps if isinstance(step, ScrollStep))
    spent = sum(spend_page() for _ in range(wanted))
    if spent < wanted:
        for _ in range(spent):
            refund_page()
        raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)


async def _check_document(
    result: AcquisitionResult,
    policy: AcquisitionPolicy,
    page: str,
    max_bytes: int,
    robots: Robots,
) -> None:
    """Refuse a document the steps ended on that the job could not have fetched itself."""
    requests = result.requests
    try:
        if result.status != Outcome.SUCCESS:
            raise AcquisitionError(result.issue_code or "step_failed", result.status)
        if not result.body:
            raise AcquisitionError("empty_document", Outcome.INVALID_DATA)
        if len(result.body.encode("utf-8")) > max_bytes:
            raise AcquisitionError("response_too_large", Outcome.PERMANENT_FAILURE)
        landed, _, _ = permitted_url(result.url, policy)
        if canonical_url(landed, policy) != page:
            await robots(landed)
    except AcquisitionError as error:
        raise AcquisitionError(error.code, error.outcome, requests=requests) from None
