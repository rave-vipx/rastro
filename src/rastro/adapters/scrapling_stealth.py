"""Explicitly consented Scrapling stealth browser, with no retry or proxy rotation."""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from rastro.adapters.playwright_browser import INSTALL, NetworkGuard, browser_flags
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    PinnedTarget,
)
from rastro.domain.models import ConfigurationError, Outcome

_active: ContextVar[bool] = ContextVar("stealth_logging", default=False)


class _RedactBrowserLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _active.get()


def _options(
    guard: NetworkGuard, policy: AcquisitionPolicy, targets: tuple[PinnedTarget, ...]
) -> dict[str, Any]:
    return {
        "page_setup": guard.setup,
        "extra_flags": browser_flags(targets),
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
        "network_idle": True,
    }


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
        token = _active.set(True)
        try:
            response = await self.fetcher.async_fetch(
                target.url, **_options(guard, policy, targets)
            )
            return guard.result(response.url, response.html_content, response.status, max_bytes)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                guard.issue or "browser_navigation_failed", Outcome.TRANSIENT_FAILURE
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
            manager = self.session_class(**_options(guard, policy, targets))
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

    async def render(
        self, target: PinnedTarget, *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        self._guard.begin(policy)
        token = _active.set(True)
        try:
            response = await self._engine.fetch(target.url)
            return self._guard.result(
                response.url, response.html_content, response.status, max_bytes
            )
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                self._guard.issue or "browser_navigation_failed", Outcome.TRANSIENT_FAILURE
            ) from None
        finally:
            _active.reset(token)

    async def scroll(
        self, *, policy: AcquisitionPolicy, max_bytes: int, settle_ms: int
    ) -> AcquisitionResult:
        # Scrapling closes each page when fetch returns; there is no live page to scroll.
        raise AcquisitionError("scroll_unsupported", Outcome.PERMANENT_FAILURE)
