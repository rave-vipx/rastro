"""Map job-file browser controls onto Playwright calls and Scrapling session options."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from rastro.adapters.browser_network import route_allowed
from rastro.application.browser_policy import (
    blocked_by_controls,
    cdp_endpoint,
    profile_directory,
    region_options,
)
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.browser_controls import BrowserControls
from rastro.domain.models import ConfigurationError

__all__ = [
    "MAX_SCREENSHOT_BYTES",
    "attach_screenshot",
    "context_options",
    "request_allowed",
    "scrapling_controls",
    "settle",
    "steps_performer",
    "wait_until",
]

MAX_SCREENSHOT_BYTES = 2_000_000
_HEAVY = {"image", "media", "font"}


def request_allowed(url: str, resource_type: str, policy: AcquisitionPolicy) -> bool:
    """The guard's per-request check, with the job's blocked domains and resource choice."""
    controls = policy.browser
    if blocked_by_controls(url, controls):
        return False
    if not controls.disable_resources and resource_type.lower() in _HEAVY:
        resource_type = "other"  # images, media and fonts were asked for; websockets stay shut
    return route_allowed(url, resource_type, policy)


def wait_until(controls: BrowserControls) -> str:
    """Wait for network quiet, unless a selector is the readiness signal and idle was not asked."""
    if controls.network_idle or controls.wait_selector is None:
        return "networkidle"
    return "domcontentloaded"


def context_options() -> dict[str, Any]:
    """Browser context locale and timezone from the source's region."""
    return dict(region_options())


async def settle(page: Any, controls: BrowserControls, timeout_ms: float) -> None:
    """Wait for the control's selector in its state; a timeout is a navigation failure."""
    if controls.wait_selector is not None:
        await page.wait_for_selector(
            controls.wait_selector, state=controls.wait_state, timeout=timeout_ms
        )


async def attach_screenshot(
    result: AcquisitionResult, page: Any, controls: BrowserControls
) -> AcquisitionResult:
    """Add PNG evidence when asked. It is optional: a failed or oversized capture is dropped."""
    if not controls.screenshot:
        return result
    try:
        image = await page.screenshot(type="png", full_page=False)
    except Exception:
        return result
    if not isinstance(image, bytes) or len(image) > MAX_SCREENSHOT_BYTES:
        return result
    return replace(result, screenshot=image)


def scrapling_controls(controls: BrowserControls) -> dict[str, Any]:
    """Scrapling session options for the controls; the grants were checked before this runs."""
    options: dict[str, Any] = {
        "disable_resources": controls.disable_resources,
        "block_ads": controls.block_ads,
        "network_idle": controls.network_idle,
        "pierce_shadow": controls.pierce_shadow,
        "real_chrome": controls.real_chrome,
        **region_options(),
    }
    if controls.blocked_domains:
        options["blocked_domains"] = set(controls.blocked_domains)
    if controls.wait_selector is not None:
        options["wait_selector"] = controls.wait_selector
        options["wait_selector_state"] = controls.wait_state
    if controls.cdp_url_env is not None:
        options["cdp_url"] = cdp_endpoint(controls.cdp_url_env)
    if controls.session_profile is not None:
        options["user_data_dir"] = str(profile_directory(controls.session_profile))
    return options


def steps_performer() -> Callable[..., Any]:
    """The page-steps runner, imported on first use; a clear error when it is not installed."""
    try:
        return importlib.import_module("rastro.adapters.browser_steps").perform_steps  # type: ignore[no-any-return]
    except ImportError:
        raise ConfigurationError("page steps are not available in this installation") from None
