"""One scroll step of a source's open browser context, under the shared budget."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from dataclasses import replace
from time import perf_counter

from rastro.application.acquisition_state import PAGINATION_SCOPE, _Host, check_page_request
from rastro.application.browser_policy import page_policy, refused_step, scroll_failure
from rastro.application.browser_sessions import SOURCE_SESSIONS
from rastro.application.destinations import permitted_url
from rastro.application.operator_trust import current_trust
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import ConfigurationError, Outcome, Source
from rastro.ports.browser import BrowserSessions


async def scroll_step(
    renderer: object,
    base: AcquisitionPolicy,
    hosts: MutableMapping[str, _Host],
    slots: asyncio.Semaphore,
    source: Source,
    *,
    settle_ms: int,
    max_bytes: int,
) -> AcquisitionResult:
    scope = SOURCE_SESSIONS.get()
    if scope is None or not isinstance(renderer, BrowserSessions):
        raise ConfigurationError("Scroll needs a browser renderer that keeps one session")
    policy = base.with_grants(current_trust().network_grants())
    started, granted = perf_counter(), 0
    try:
        canonical, hostname, _ = permitted_url(source.url, policy)
        policy = page_policy(policy, canonical)  # the page's own scheme bounds every step
        session = scope.showing(renderer, host=hostname, url=canonical)
        if session is None:  # a page rendered outside the session has no page to scroll
            raise ConfigurationError("This page is not the one the session last rendered")
        host = hosts[hostname]
        async with host.lock, slots:
            await asyncio.sleep(max(0, host.next_request - perf_counter()))
            check_page_request(source.url)
            budget = PAGINATION_SCOPE.get()
            # the step was charged one request above, which its own first transfer repays
            granted = budget.remaining + 1 if budget else policy.max_requests
            step = policy.model_copy(update={"max_requests": granted})
            try:
                async with asyncio.timeout(base.request_timeout_seconds + settle_ms / 1000):
                    result = await session.scroll(
                        policy=step, max_bytes=max_bytes, settle_ms=settle_ms
                    )
            except BaseException:
                await scope.discard(renderer)  # the page's host is unknown now
                raise
            finally:
                host.next_request = max(host.next_request, perf_counter() + host.interval)
        refusal = refused_step(result, policy, max_bytes, page=canonical)
        if await scope.leave_foreign(renderer, result.url):
            refusal = refusal or AcquisitionError(
                "scroll_navigated", Outcome.PERMANENT_FAILURE, requests=result.requests
            )
        elif refusal and refusal.code == "scroll_navigated":
            scope.mark_rendered(renderer, None)  # keep the records; no page to scroll
        if refusal:
            raise refusal
        return replace(result, acquisition_seconds=perf_counter() - started)
    except TimeoutError:
        # The browser's own count died with the step: charge all it was granted, never less.
        return scroll_failure(source, Outcome.TRANSIENT_FAILURE, "operation_timeout", granted)
    except AcquisitionError as error:
        return scroll_failure(source, error.outcome, error.code, error.requests)
