"""Open a Chromium context for a source and load one page in it, under its browser controls."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from rastro.adapters.browser_controls_apply import (
    attach_screenshot,
    context_options,
    settle,
    wait_until,
)
from rastro.adapters.browser_network import launch_options
from rastro.adapters.network_capture import page_document
from rastro.application.browser_policy import cdp_endpoint, profile_directory
from rastro.application.browser_proxy import BROWSER_PROXY
from rastro.application.http_policy import USER_AGENT
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, PinnedTarget

__all__ = ["load_page", "open_context"]


async def open_context(
    stack: AsyncExitStack,
    driver: Any,
    targets: tuple[PinnedTarget, ...],
    policy: AcquisitionPolicy,
) -> Any:
    """One locked-down context: a remote browser, a persistent profile or a fresh launch.

    Closing is registered on ``stack``. The grants for the remote and profile paths were
    checked before any renderer existed.
    """
    controls = policy.browser
    options: dict[str, Any] = {
        "user_agent": USER_AGENT,
        "accept_downloads": False,
        "permissions": [],
        "service_workers": "block",
        **context_options(),
    }
    if controls.cdp_url_env is not None:
        browser = await driver.chromium.connect_over_cdp(cdp_endpoint(controls.cdp_url_env))
        stack.push_async_callback(browser.close)
        return await browser.new_context(**options)
    launch = launch_options(targets, BROWSER_PROXY.get())
    if controls.real_chrome:
        launch["channel"] = "chrome"
    if controls.session_profile is not None:
        directory = profile_directory(controls.session_profile)
        context = await driver.chromium.launch_persistent_context(
            str(directory), **launch, **options
        )
        stack.push_async_callback(context.close)
        return context
    browser = await driver.chromium.launch(**launch)
    stack.push_async_callback(browser.close)
    return await browser.new_context(**options)


async def load_page(
    page: Any, guard: Any, url: str, policy: AcquisitionPolicy, max_bytes: int
) -> AcquisitionResult:
    """Navigate, wait for the selector if one was set, then judge and capture the document."""
    controls = policy.browser
    timeout_ms = policy.request_timeout_seconds * 1000
    response = await page.goto(url, timeout=timeout_ms, wait_until=wait_until(controls))
    await settle(page, controls, timeout_ms)
    document = await page_document(page, controls)  # open shadow roots with pierce_shadow
    result = guard.result(page.url, document, response.status if response else 0, max_bytes)
    return await attach_screenshot(result, page, controls)
