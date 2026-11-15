"""Explicitly consented Scrapling stealth browser, with no retry or proxy rotation."""

from __future__ import annotations

import importlib
import logging
from contextvars import ContextVar

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


class ScraplingStealth:
    def __init__(self) -> None:
        try:
            self.fetcher = importlib.import_module(
                "scrapling.fetchers.stealth_chrome"
            ).StealthyFetcher
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
                target.url,
                page_setup=guard.setup,
                extra_flags=browser_flags(targets),
                additional_args={
                    "service_workers": "block",
                    "accept_downloads": False,
                    "permissions": [],
                    "ignore_https_errors": False,
                },
                timeout=policy.request_timeout_seconds * 1000,
                retries=1,
                solve_cloudflare=policy.solve_challenges,
                google_search=False,
                dns_over_https=False,
                block_webrtc=True,
                network_idle=True,
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
