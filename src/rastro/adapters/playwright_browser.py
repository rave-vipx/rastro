"""Plain Chromium and shared browser network restrictions (SEC-005/008)."""

from __future__ import annotations

import importlib
import weakref
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

from rastro.adapters.browser_controls_apply import request_allowed
from rastro.adapters.browser_network import (
    browser_flags,
    launch_options,
    proxy_option,
    resolver_rules,
    route_allowed,
)
from rastro.adapters.playwright_launch import load_page, open_context
from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    PinnedTarget,
)
from rastro.domain.models import ConfigurationError, Outcome

__all__ = [
    "INSTALL",
    "NetworkGuard",
    "PlaywrightBrowser",
    "browser_flags",
    "launch_options",
    "proxy_option",
    "resolver_rules",
    "route_allowed",
]

if TYPE_CHECKING:
    from rastro.adapters.playwright_session import PlaywrightSession

INSTALL = (
    "Install pip install 'rastro-engine[browser]' then playwright install chromium "
    "(stealth: scrapling install)"
)


class NetworkGuard:
    def __init__(self, policy: AcquisitionPolicy):
        self.policy = policy
        self.requests = 0
        self.issue: str | None = None
        self.challenged = False  # a document answered with a challenge during this step
        # A reused page stays open between steps: only an active step may make requests.
        self.active = True
        self._guarded: weakref.WeakSet[Any] = weakref.WeakSet()

    def begin(self, policy: AcquisitionPolicy) -> None:
        """Arm the guard for the next navigation or scroll step, under the budget left for it."""
        self.policy, self.requests, self.issue, self.active = policy, 0, None, True
        self.challenged = False

    def end(self) -> None:
        """Idle: every request of a page that stays open between steps fails and is not counted."""
        self.active = False

    async def setup(self, page: Any) -> None:
        if page in self._guarded:  # a pooled page is set up once; stacking would double-count
            return
        try:

            async def route(route: Any) -> None:
                request = route.request
                if (
                    not self.active
                    or request.frame.page != page
                    or not request_allowed(request.url, request.resource_type, self.policy)
                ):
                    await route.abort()
                else:
                    await route.continue_()

            await page.context.route("**/*", route)
            await page.context.route_web_socket("**/*", lambda ws: ws.close())
            session = await page.context.new_cdp_session(page)

            async def paused(event: dict[str, Any]) -> None:
                allowed = self.active and request_allowed(
                    event["request"]["url"], event.get("resourceType", ""), self.policy
                )
                if self.active:
                    if self.requests >= self.policy.max_requests:
                        self.issue, allowed = "request_limit", False
                    if allowed:
                        self.requests += 1
                    elif event.get("resourceType") == "Document":
                        self.issue = self.issue or "destination_denied"
                await session.send(
                    "Fetch.continueRequest" if allowed else "Fetch.failRequest",
                    {
                        "requestId": event["requestId"],
                        **({} if allowed else {"errorReason": "BlockedByClient"}),
                    },
                )

            async def responded(event: dict[str, Any]) -> None:
                response = event.get("response", {})
                headers = {k.lower(): v for k, v in response.get("headers", {}).items()}
                if (
                    event.get("type") == "Document"
                    and str(headers.get("cf-mitigated", "")).lower() == "challenge"
                ):
                    self.challenged = True

            session.on("Fetch.requestPaused", paused)
            session.on("Network.responseReceived", responded)
            await session.send("Network.enable", {})
            await session.send(
                "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
            )
            self._guarded.add(page)
        except Exception:
            # Scrapling catches setup errors; a closed page cannot navigate unguarded.
            await page.close()
            raise AcquisitionError("browser_guard_failed", Outcome.PERMANENT_FAILURE) from None

    def result(self, url: str, body: str, status: int, max_bytes: int) -> AcquisitionResult:
        self.end()  # the step is over, whatever the outcome
        try:
            permitted_url(url, self.policy)
        except AcquisitionError as error:
            raise AcquisitionError(error.code, error.outcome, requests=self.requests) from None
        if self.issue:
            # Same outcome as the HTTP path's exhausted request budget.
            outcome = (
                Outcome.PERMANENT_FAILURE
                if self.issue == "request_limit"
                else Outcome.ACCESS_RESTRICTION
            )
            raise AcquisitionError(self.issue, outcome, requests=self.requests)
        if len(body.encode("utf-8")) > max_bytes:
            raise AcquisitionError(
                "response_too_large", Outcome.PERMANENT_FAILURE, requests=self.requests
            )
        if any(
            marker in body.lower()
            for marker in ("cf-chl-", "cf-turnstile", "g-recaptcha", "hcaptcha")
        ):
            raise AcquisitionError(
                "challenge_unsolved", Outcome.ACCESS_RESTRICTION, requests=self.requests
            )
        return AcquisitionResult(
            url,
            body,
            Outcome.SUCCESS,
            None,
            http_status=status,
            requests=self.requests,
            challenge_solved=self.challenged,  # challenged, then a clean page came back
        )


class PlaywrightBrowser:
    def __init__(self) -> None:
        try:
            self.api = importlib.import_module("playwright.async_api")
        except ImportError:
            raise ConfigurationError(INSTALL) from None

    async def render(
        self,
        target: PinnedTarget,
        *,
        policy: AcquisitionPolicy,
        max_bytes: int,
        targets: tuple[PinnedTarget, ...],
    ) -> AcquisitionResult:
        guard = NetworkGuard(policy)
        try:
            async with AsyncExitStack() as stack:
                driver = await stack.enter_async_context(self.api.async_playwright())
                context = await open_context(stack, driver, targets, policy)
                page = await context.new_page()
                await guard.setup(page)
                return await load_page(page, guard, target.url, policy, max_bytes)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=guard.requests,
            ) from None

    @asynccontextmanager
    async def session(
        self, targets: tuple[PinnedTarget, ...], *, policy: AcquisitionPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        """One Chromium context for one source: same guard, flags and blocks as ``render``."""
        guard = NetworkGuard(policy)
        async with AsyncExitStack() as stack:
            try:
                driver = await stack.enter_async_context(self.api.async_playwright())
                context = await open_context(stack, driver, targets, policy)
            except Exception:
                raise AcquisitionError(
                    "browser_navigation_failed", Outcome.TRANSIENT_FAILURE
                ) from None
            from rastro.adapters.playwright_session import PlaywrightSession

            yield PlaywrightSession(context, guard)
