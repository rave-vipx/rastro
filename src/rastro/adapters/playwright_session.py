"""One Chromium context for one source: navigations and scroll steps share a page (N2, N9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from rastro.adapters.browser_controls_apply import attach_screenshot, steps_performer
from rastro.adapters.playwright_browser import NetworkGuard
from rastro.adapters.playwright_launch import load_page
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    PinnedTarget,
)
from rastro.domain.models import Outcome


class PlaywrightSession:
    """Navigations and scroll steps share one context and one page, under one NetworkGuard."""

    def __init__(self, context: Any, guard: NetworkGuard) -> None:
        self._context, self._guard = context, guard
        self._page: Any = None

    async def render(
        self, target: PinnedTarget, *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        self._guard.begin(policy)
        try:
            if self._page is None:
                page = await self._context.new_page()
                await self._guard.setup(page)  # a failed setup closes the page and raises
                self._page = page
            return await load_page(self._page, self._guard, target.url, policy, max_bytes)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                self._guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=self._guard.requests,
            ) from None
        finally:
            self._guard.end()

    async def scroll(
        self, *, policy: AcquisitionPolicy, max_bytes: int, settle_ms: int
    ) -> AcquisitionResult:
        if self._page is None:
            raise AcquisitionError("scroll_without_page", Outcome.PERMANENT_FAILURE)
        self._guard.begin(policy)
        try:
            await self._page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            await self._page.wait_for_timeout(settle_ms)
            result = self._guard.result(self._page.url, await self._page.content(), 200, max_bytes)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                self._guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=self._guard.requests,
            ) from None
        finally:
            self._guard.end()
        return replace(result, requests=max(1, result.requests))

    async def run_steps(
        self, steps: Sequence[Any], *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        """Run page steps on the open page, then return its document under the same guard."""
        if self._page is None:
            raise AcquisitionError("step_failed", Outcome.PERMANENT_FAILURE)
        perform_steps = steps_performer()
        self._guard.begin(policy)
        try:
            await perform_steps(
                self._page,
                steps,
                guard=self._guard,
                timeout_ms=policy.request_timeout_seconds * 1000,
            )
            result = self._guard.result(self._page.url, await self._page.content(), 200, max_bytes)
            result = await attach_screenshot(result, self._page, policy.browser)
        except AcquisitionError:
            raise
        except Exception:
            raise AcquisitionError(
                self._guard.issue or "browser_navigation_failed",
                Outcome.TRANSIENT_FAILURE,
                requests=self._guard.requests,
            ) from None
        finally:
            self._guard.end()
        return replace(result, requests=max(1, result.requests))
