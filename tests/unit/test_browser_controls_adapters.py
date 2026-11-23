"""Browser controls reach the Chromium and stealth adapters; grants and secrets stay safe."""

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from rastro.application.browser_policy import (
    BROWSER_REGION,
    cdp_endpoint,
    profile_directory,
)
from rastro.application.capability_gate import CapabilityRefused, policy_capabilities, require
from rastro.domain.acquisition import AcquisitionPolicy, PinnedTarget
from rastro.domain.browser_controls import BrowserControls, HttpOptions
from rastro.domain.models import ConfigurationError

TARGET = PinnedTarget("https://example.com/a", "example.com", 443, "93.184.216.34")


def policy(**controls):
    return AcquisitionPolicy(
        allowed_hosts=("example.com",), render="browser", browser=BrowserControls(**controls)
    )


class Page:
    def __init__(self, log):
        self.log, self.url = log, ""

    async def goto(self, url, **kwargs):
        self.url = url
        self.log.append(("goto", kwargs["wait_until"]))
        return SimpleNamespace(status=200)

    async def wait_for_selector(self, selector, **kwargs):
        self.log.append(("wait_for_selector", selector, kwargs["state"]))

    async def content(self):
        return "<p>ok</p>"

    async def screenshot(self, **kwargs):
        return b"\x89PNG-bytes"


class Context:
    def __init__(self, log):
        self.log = log

    async def new_page(self):
        return Page(self.log)


class Browser:
    def __init__(self, log):
        self.log, self.contexts = log, []

    async def new_context(self, **options):
        self.log.append(("context", options))
        return Context(self.log)

    async def close(self):
        pass


class Chromium:
    def __init__(self, log):
        self.log = log

    async def launch(self, **options):
        self.log.append(("launch", options))
        return Browser(self.log)

    async def connect_over_cdp(self, endpoint):
        self.log.append(("cdp", endpoint))
        return Browser(self.log)


@pytest.fixture
def browser(monkeypatch):
    from rastro.adapters.playwright_browser import NetworkGuard, PlaywrightBrowser

    async def setup(self, page):
        return None

    monkeypatch.setattr(NetworkGuard, "setup", setup)
    monkeypatch.setattr(PlaywrightBrowser, "__init__", lambda self: None)
    renderer, log = PlaywrightBrowser(), []

    @asynccontextmanager
    async def async_playwright():
        yield SimpleNamespace(chromium=Chromium(log))

    renderer.api = SimpleNamespace(async_playwright=async_playwright)
    return renderer, log


@pytest.mark.asyncio
async def test_wait_selector_passed(browser):
    renderer, log = browser
    current = policy(wait_selector="#grid", wait_state="visible")
    async with renderer.session((TARGET,), policy=current) as session:
        await session.render(TARGET, policy=current, max_bytes=1000)
    assert ("wait_for_selector", "#grid", "visible") in log
    assert ("goto", "domcontentloaded") in log
    from rastro.adapters.scrapling_stealth import _options

    options = _options(SimpleNamespace(setup=None), current, (TARGET,), None)
    assert options["wait_selector"] == "#grid" and options["wait_selector_state"] == "visible"
    assert options["network_idle"] is False


def test_blocked_domains_and_block_ads():
    from rastro.adapters.browser_controls_apply import request_allowed

    plain = policy(blocked_domains=("tracker.example",))
    assert not request_allowed("https://tracker.example/x.js", "script", plain)
    assert not request_allowed("https://cdn.tracker.example/x.js", "script", plain)
    assert request_allowed("https://example.com/app.js", "script", plain)
    ad = "https://ads.doubleclick.net/pixel"
    assert request_allowed(ad, "script", plain) is False  # not allow-listed anyway
    ads = AcquisitionPolicy(
        allowed_hosts=("example.com", "ads.doubleclick.net"),
        render="browser",
        browser=BrowserControls(block_ads=True),
    )
    open_ads = ads.model_copy(update={"browser": BrowserControls()})
    assert not request_allowed(ad, "script", ads)
    assert request_allowed(ad, "script", open_ads)
    assert not request_allowed("https://example.com/a.png", "image", plain)
    keep = policy(disable_resources=False)
    assert request_allowed("https://example.com/a.png", "image", keep)
    assert not request_allowed("https://example.com/ws", "websocket", keep)


def test_cdp_requires_grant_and_env_name(monkeypatch):
    current = policy(cdp_url_env="RASTRO_TEST_CDP")
    assert "cdp" in policy_capabilities(current)
    with pytest.raises(CapabilityRefused):
        require(policy_capabilities(current))  # grant-only, even for the operator
    with pytest.raises(ValueError):
        BrowserControls(cdp_url_env="http://localhost:9222")  # an env name, never the value
    monkeypatch.delenv("RASTRO_TEST_CDP", raising=False)
    with pytest.raises(ConfigurationError) as error:
        cdp_endpoint("RASTRO_TEST_CDP")
    assert "RASTRO_TEST_CDP" in str(error.value)
    monkeypatch.setenv("RASTRO_TEST_CDP", "ftp://secret-host:1/token")
    with pytest.raises(ConfigurationError) as error:
        cdp_endpoint("RASTRO_TEST_CDP")
    assert "secret-host" not in str(error.value) and "token" not in repr(error.value)
    monkeypatch.setenv("RASTRO_TEST_CDP", "ws://127.0.0.1:9222/devtools/browser/x")
    assert cdp_endpoint("RASTRO_TEST_CDP").startswith("ws://")


def test_session_profile_is_a_name_inside_the_operators_directory(tmp_path):
    root = {"RASTRO_PROFILE_DIR": str(tmp_path)}
    assert profile_directory("shop", root) == tmp_path / "shop"
    with pytest.raises(ConfigurationError):
        profile_directory("shop", {})
    with pytest.raises(ConfigurationError):
        profile_directory("..", root)


@pytest.mark.asyncio
async def test_screenshot_evidence_optional_and_redacted_path(browser):
    renderer, _ = browser
    off = policy()
    async with renderer.session((TARGET,), policy=off) as session:
        assert (await session.render(TARGET, policy=off, max_bytes=1000)).screenshot is None
    on = policy(screenshot=True)
    async with renderer.session((TARGET,), policy=on) as session:
        result = await session.render(TARGET, policy=on, max_bytes=1000)
    assert result.screenshot == b"\x89PNG-bytes"
    assert "PNG" not in repr(result)  # evidence is never printed
    assert not hasattr(result, "screenshot_path")


@pytest.mark.asyncio
async def test_screenshot_failure_keeps_the_document(browser, monkeypatch):
    renderer, _ = browser
    monkeypatch.setattr(Page, "screenshot", lambda self, **kw: (_ for _ in ()).throw(OSError("x")))
    on = policy(screenshot=True)
    async with renderer.session((TARGET,), policy=on) as session:
        result = await session.render(TARGET, policy=on, max_bytes=1000)
    assert result.body == "<p>ok</p>" and result.screenshot is None


@pytest.mark.asyncio
async def test_region_timezone_applied(browser):
    renderer, log = browser
    current = policy()
    token = BROWSER_REGION.set(("es-ES", "Europe/Madrid"))
    try:
        async with renderer.session((TARGET,), policy=current):
            pass
        from rastro.adapters.scrapling_stealth import _options

        stealth = _options(SimpleNamespace(setup=None), current, (TARGET,), None)
    finally:
        BROWSER_REGION.reset(token)
    context = next(entry[1] for entry in log if entry[0] == "context")
    assert context["locale"] == "es-ES" and context["timezone_id"] == "Europe/Madrid"
    assert stealth["locale"] == "es-ES" and stealth["timezone_id"] == "Europe/Madrid"
    log.clear()
    async with renderer.session((TARGET,), policy=current):
        pass
    assert "locale" not in next(entry[1] for entry in log if entry[0] == "context")


def test_policy_carries_browser_and_http_options():
    current = AcquisitionPolicy(
        allowed_hosts=("example.com",), http=HttpOptions(impersonate="chrome")
    )
    assert current.browser == BrowserControls()
    assert policy_capabilities(current) == {"impersonation"}


@pytest.mark.asyncio
async def test_steps_run_through_the_page_steps_module(browser, monkeypatch):
    renderer, _ = browser
    current = policy()
    async with renderer.session((TARGET,), policy=current) as session:
        monkeypatch.setitem(sys.modules, "rastro.adapters.browser_steps", None)
        await session.render(TARGET, policy=current, max_bytes=1000)
        with pytest.raises(ConfigurationError, match="page steps"):
            await session.run_steps(("a",), policy=current, max_bytes=1000)
        seen = []

        async def perform_steps(page, steps, *, guard, timeout_ms):
            seen.append((tuple(steps), timeout_ms))

        module = SimpleNamespace(perform_steps=perform_steps)
        monkeypatch.setitem(sys.modules, "rastro.adapters.browser_steps", module)
        result = await session.run_steps(("a",), policy=current, max_bytes=1000)
    from rastro.application.page_steps import steps_budget_ms

    budget = steps_budget_ms(("a",), current)  # type: ignore[arg-type]
    assert seen == [(("a",), budget)] and result.body == "<p>ok</p>"
