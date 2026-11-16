"""Plain Chromium and shared browser network restrictions (SEC-005/008)."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

from rastro.application.destinations import permitted_url
from rastro.application.http_policy import USER_AGENT
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    PinnedTarget,
)
from rastro.domain.models import ConfigurationError, Outcome

if TYPE_CHECKING:
    from rastro.adapters.playwright_session import PlaywrightSession

INSTALL = (
    "Install pip install 'rastro-engine[browser]' then playwright install chromium "
    "(stealth: scrapling install)"
)


def route_allowed(url: str, resource_type: str, policy: AcquisitionPolicy) -> bool:
    if resource_type.lower() in {"image", "media", "font", "websocket"}:
        return False
    try:
        permitted_url(url, policy)
        return True
    except AcquisitionError:
        return False


def resolver_rules(targets: tuple[PinnedTarget, ...]) -> str:
    return ", ".join(
        [*(f"MAP {t.host} {t.address}" for t in targets), "MAP * ~NOTFOUND", "EXCLUDE localhost"]
    )


def browser_flags(targets: tuple[PinnedTarget, ...]) -> list[str]:
    return [
        f"--host-resolver-rules={resolver_rules(targets)}",
        "--disable-quic",
        "--no-proxy-server",
        "--disable-background-networking",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]


class NetworkGuard:
    def __init__(self, policy: AcquisitionPolicy):
        self.policy = policy
        self.requests = 0
        self.issue: str | None = None

    def begin(self, policy: AcquisitionPolicy) -> None:
        """Count the next navigation or scroll step on its own, under the budget left for it."""
        self.policy, self.requests, self.issue = policy, 0, None

    async def setup(self, page: Any) -> None:
        try:

            async def route(route: Any) -> None:
                request = route.request
                if request.frame.page != page or not route_allowed(
                    request.url, request.resource_type, self.policy
                ):
                    await route.abort()
                else:
                    await route.continue_()

            await page.context.route("**/*", route)
            await page.context.route_web_socket("**/*", lambda ws: ws.close())
            session = await page.context.new_cdp_session(page)

            async def paused(event: dict[str, Any]) -> None:
                allowed = route_allowed(
                    event["request"]["url"], event.get("resourceType", ""), self.policy
                )
                if self.requests >= self.policy.max_requests:
                    self.issue = "request_limit"
                    allowed = False
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

            session.on("Fetch.requestPaused", paused)
            await session.send(
                "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
            )
        except Exception:
            # Scrapling catches setup errors; a closed page cannot navigate unguarded.
            await page.close()
            raise AcquisitionError("browser_guard_failed", Outcome.PERMANENT_FAILURE) from None

    def result(self, url: str, body: str, status: int, max_bytes: int) -> AcquisitionResult:
        permitted_url(url, self.policy)
        if self.issue:
            # Same outcome as the HTTP path's exhausted request budget.
            outcome = (
                Outcome.PERMANENT_FAILURE
                if self.issue == "request_limit"
                else Outcome.ACCESS_RESTRICTION
            )
            raise AcquisitionError(self.issue, outcome)
        if len(body.encode("utf-8")) > max_bytes:
            raise AcquisitionError("response_too_large", Outcome.PERMANENT_FAILURE)
        if any(
            marker in body.lower()
            for marker in ("cf-chl-", "cf-turnstile", "g-recaptcha", "hcaptcha")
        ):
            raise AcquisitionError("challenge_unsolved", Outcome.ACCESS_RESTRICTION)
        return AcquisitionResult(
            url, body, Outcome.SUCCESS, None, http_status=status, requests=self.requests
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
            async with self.api.async_playwright() as driver:
                browser = await driver.chromium.launch(args=browser_flags(targets))
                try:
                    context = await browser.new_context(
                        user_agent=USER_AGENT,
                        accept_downloads=False,
                        permissions=[],
                        service_workers="block",
                    )
                    page = await context.new_page()
                    await guard.setup(page)
                    response = await page.goto(
                        target.url,
                        timeout=policy.request_timeout_seconds * 1000,
                        wait_until="networkidle",
                    )
                    return guard.result(
                        page.url,
                        await page.content(),
                        response.status if response else 0,
                        max_bytes,
                    )
                finally:
                    await browser.close()
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                guard.issue or "browser_navigation_failed", Outcome.TRANSIENT_FAILURE
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
                browser = await driver.chromium.launch(args=browser_flags(targets))
                stack.push_async_callback(browser.close)
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    accept_downloads=False,
                    permissions=[],
                    service_workers="block",
                )
            except Exception:
                raise AcquisitionError(
                    "browser_navigation_failed", Outcome.TRANSIENT_FAILURE
                ) from None
            from rastro.adapters.playwright_session import PlaywrightSession

            yield PlaywrightSession(context, guard)
