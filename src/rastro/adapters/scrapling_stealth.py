"""Scrapling stealth browser behind Rastro's grants, guard and proxy routes.

Rastro picks the proxy (one per render, or one per source context) and checks every target
itself, so Scrapling's own retry and proxy rotation stay off.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from rastro.adapters.browser_controls_apply import (
    MAX_SCREENSHOT_BYTES,
    scrapling_controls,
    steps_performer,
)
from rastro.adapters.playwright_browser import INSTALL, NetworkGuard, browser_flags, proxy_option
from rastro.application.browser_proxy import BROWSER_PROXY
from rastro.application.page_steps import steps_budget_ms
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    PinnedTarget,
    ProxyRoute,
)
from rastro.domain.browser_controls import BrowserControls
from rastro.domain.models import ConfigurationError, Outcome

_active: ContextVar[bool] = ContextVar("stealth_logging", default=False)


class _RedactBrowserLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _active.get()


def _options(
    guard: NetworkGuard,
    policy: AcquisitionPolicy,
    targets: tuple[PinnedTarget, ...],
    proxy: ProxyRoute | None,
) -> dict[str, Any]:
    route = proxy_option(proxy)
    return {
        **({"proxy": route} if route is not None else {}),
        "page_setup": guard.setup,
        "extra_flags": browser_flags(targets, proxy),
        "additional_args": {
            "service_workers": "block",
            "accept_downloads": False,
            "permissions": [],
            "ignore_https_errors": False,
        },
        "timeout": policy.request_timeout_seconds * 1000,
        "retries": 1,
        "solve_cloudflare": policy.solve_challenges,
        "google_search": False,
        "dns_over_https": False,
        "block_webrtc": True,
        **scrapling_controls(policy.browser),
    }


def _page_action(
    policy: AcquisitionPolicy, guard: NetworkGuard, steps: Sequence[Any], evidence: list[bytes]
) -> Any:
    """The hook Scrapling runs after navigation: page steps, then optional PNG evidence."""
    controls: BrowserControls = policy.browser

    async def action(page: Any) -> Any:
        if steps:
            await steps_performer()(
                page, steps, guard=guard, timeout_ms=steps_budget_ms(tuple(steps), policy)
            )
        if controls.screenshot:
            try:
                image = await page.screenshot(type="png", full_page=False)
            except Exception:
                image = b""
            if isinstance(image, bytes) and 0 < len(image) <= MAX_SCREENSHOT_BYTES:
                evidence.append(image)
        return page

    return action


def _with_evidence(result: AcquisitionResult, evidence: list[bytes]) -> AcquisitionResult:
    return replace(result, screenshot=evidence[0]) if evidence else result


class ScraplingStealth:
    def __init__(self) -> None:
        try:
            self.fetcher = importlib.import_module(
                "scrapling.fetchers.stealth_chrome"
            ).StealthyFetcher
            self.session_class = importlib.import_module(
                "scrapling.fetchers.stealth_chrome"
            ).AsyncStealthySession
            logger = importlib.import_module("scrapling.core.utils").log
        except ImportError:
            raise ConfigurationError(INSTALL) from None
        if not any(isinstance(f, _RedactBrowserLogs) for f in logger.filters):
            logger.addFilter(_RedactBrowserLogs())

    async def render(
        self,
        target: PinnedTarget,
        *,
        policy: AcquisitionPolicy,
        max_bytes: int,
        targets: tuple[PinnedTarget, ...],
    ) -> AcquisitionResult:
        guard = NetworkGuard(policy)
        evidence: list[bytes] = []
        token = _active.set(True)
        try:
            response = await self.fetcher.async_fetch(
                target.url,
                page_action=_page_action(policy, guard, (), evidence),
                **_options(guard, policy, targets, BROWSER_PROXY.get()),
            )
            result = guard.result(response.url, response.html_content, response.status, max_bytes)
            return _with_evidence(result, evidence)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=guard.requests,
            ) from None
        finally:
            _active.reset(token)

    @asynccontextmanager
    async def session(
        self, targets: tuple[PinnedTarget, ...], *, policy: AcquisitionPolicy
    ) -> AsyncIterator[StealthSession]:
        """One stealth context for one source (pages share its cookies); same guard and flags."""
        guard = NetworkGuard(policy)
        token = _active.set(True)
        try:
            manager = self.session_class(**_options(guard, policy, targets, BROWSER_PROXY.get()))
            engine = await manager.__aenter__()
        except Exception:
            raise AcquisitionError("browser_navigation_failed", Outcome.TRANSIENT_FAILURE) from None
        finally:
            _active.reset(token)
        try:
            yield StealthSession(engine, guard)
        finally:
            token = _active.set(True)
            try:
                await manager.__aexit__(None, None, None)
            finally:
                _active.reset(token)


class StealthSession:
    """Pages of one source share a context; scrolling a live page is not supported here."""

    def __init__(self, engine: Any, guard: NetworkGuard) -> None:
        self._engine, self._guard = engine, guard
        self._target: PinnedTarget | None = None

    async def render(
        self, target: PinnedTarget, *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        self._target = target
        return await self._fetch(target, (), policy=policy, max_bytes=max_bytes)

    async def run_steps(
        self, steps: Sequence[Any], *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        """Reload the page last rendered and run ``steps`` in Scrapling's after-navigation hook.

        Scrapling does not hand out the live page between fetches, so the steps act on a fresh
        load of the same URL; its transfers spend the step's budget like any other.
        """
        if self._target is None:
            raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)
        return await self._fetch(self._target, steps, policy=policy, max_bytes=max_bytes)

    async def _fetch(
        self,
        target: PinnedTarget,
        steps: Sequence[Any],
        *,
        policy: AcquisitionPolicy,
        max_bytes: int,
    ) -> AcquisitionResult:
        """Load ``target``, run page steps through Scrapling's after-navigation hook."""
        self._guard.begin(policy)
        evidence: list[bytes] = []
        token = _active.set(True)
        try:
            response = await self._engine.fetch(
                target.url, page_action=_page_action(policy, self._guard, steps, evidence)
            )
            result = self._guard.result(
                response.url, response.html_content, response.status, max_bytes
            )
            return _with_evidence(result, evidence)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                self._guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=self._guard.requests,
            ) from None
        finally:
            self._guard.end()  # Scrapling keeps the page pooled: nothing runs between fetches
            _active.reset(token)

    async def scroll(
        self, *, policy: AcquisitionPolicy, max_bytes: int, settle_ms: int
    ) -> AcquisitionResult:
        # Scrapling keeps the page pooled but Rastro does not drive scroll in it (the job layer
        # reports scroll_unsupported_stealth and never asks); this stays a hard stop.
        raise AcquisitionError("scroll_unsupported", Outcome.PERMANENT_FAILURE)
