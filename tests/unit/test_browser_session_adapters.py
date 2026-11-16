"""N2, N9, SEC-005/008: the Chromium and Scrapling sessions, against fake drivers (no browser)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, PinnedTarget
from rastro.domain.models import Outcome

TARGET = PinnedTarget("https://example.com/a", "example.com", 443, "93.184.216.34")
POLICY = AcquisitionPolicy(allowed_hosts=("example.com",), render="browser")


class Page:
    def __init__(self, log):
        self.log, self.url, self.closed = log, "", False

    async def goto(self, url, **kwargs):
        self.url = url
        self.log.append(("goto", url, kwargs["wait_until"]))
        return SimpleNamespace(status=200)

    async def content(self):
        return "<p>ok</p>"

    async def evaluate(self, script):
        self.log.append(("evaluate", script))

    async def wait_for_timeout(self, ms):
        self.log.append(("wait", ms))


class Context:
    def __init__(self, log):
        self.log = log

    async def new_page(self):
        self.log.append(("new_page",))
        return Page(self.log)


class Chromium:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    async def launch(self, args):
        if self.fail:
            raise RuntimeError("no chromium")
        self.log.append(("launch", tuple(args)))
        return Browser(self.log)


class Browser:
    def __init__(self, log):
        self.log = log

    async def new_context(self, **options):
        self.log.append(("context", options["service_workers"], options["permissions"]))
        return Context(self.log)

    async def close(self):
        self.log.append(("close",))


def fake_api(log, fail=False):
    @asynccontextmanager
    async def async_playwright():
        yield SimpleNamespace(chromium=Chromium(log, fail))

    return SimpleNamespace(async_playwright=async_playwright)


@pytest.fixture
def browser(monkeypatch):
    from rastro.adapters.playwright_browser import NetworkGuard, PlaywrightBrowser

    async def setup(self, page):
        self.log.append(("guard", page.url))

    monkeypatch.setattr(NetworkGuard, "setup", setup)
    monkeypatch.setattr(PlaywrightBrowser, "__init__", lambda self: None)
    renderer, log = PlaywrightBrowser(), []
    renderer.api = fake_api(log)
    NetworkGuard.log = log
    return renderer, log


@pytest.mark.asyncio
async def test_playwright_session_reuses_one_context_and_page(browser):
    renderer, log = browser
    async with renderer.session((TARGET,), policy=POLICY) as session:
        first = await session.render(TARGET, policy=POLICY, max_bytes=1000)
        await session.render(TARGET, policy=POLICY, max_bytes=1000)
        stepped = await session.scroll(policy=POLICY, max_bytes=1000, settle_ms=250)
    kinds = [entry[0] for entry in log]
    assert kinds.count("launch") == kinds.count("context") == 1
    assert kinds.count("new_page") == kinds.count("guard") == 1  # one page, one guard setup
    assert kinds.count("goto") == 2 and kinds[-1] == "close"
    assert ("wait", 250) in log and ("goto", TARGET.url, "networkidle") in log
    assert ("context", "block", []) in log
    assert first.body == stepped.body == "<p>ok</p>" and stepped.requests >= 1


@pytest.mark.asyncio
async def test_playwright_session_scroll_needs_a_page_and_launch_failure_is_typed(browser):
    renderer, log = browser
    async with renderer.session((TARGET,), policy=POLICY) as session:
        with pytest.raises(AcquisitionError) as error:
            await session.scroll(policy=POLICY, max_bytes=1000, settle_ms=100)
    assert error.value.code == "scroll_without_page"
    renderer.api = fake_api(log, fail=True)
    with pytest.raises(AcquisitionError) as error:
        async with renderer.session((TARGET,), policy=POLICY):
            pass  # pragma: no cover
    assert error.value.code == "browser_navigation_failed"
    assert error.value.outcome == Outcome.TRANSIENT_FAILURE


@pytest.mark.asyncio
async def test_playwright_session_wraps_navigation_errors(browser, monkeypatch):
    renderer, _ = browser

    async def broken(self, url, **kwargs):
        raise RuntimeError("secret page content")

    monkeypatch.setattr(Page, "goto", broken)
    async with renderer.session((TARGET,), policy=POLICY) as session:
        with pytest.raises(AcquisitionError) as error:
            await session.render(TARGET, policy=POLICY, max_bytes=1000)
    assert error.value.code == "browser_navigation_failed"
    assert "secret" not in repr(error.value)


class Engine:
    def __init__(self):
        self.urls, self.entered, self.exited = [], 0, 0

    async def fetch(self, url):
        self.urls.append(url)
        return SimpleNamespace(url=url, html_content="<p>ok</p>", status=200)


class Manager:
    engine = None

    def __init__(self, **options):
        self.options = options
        Manager.engine = Engine()

    async def __aenter__(self):
        Manager.engine.entered += 1
        return Manager.engine

    async def __aexit__(self, *exc):
        Manager.engine.exited += 1


@pytest.mark.asyncio
async def test_stealth_session_shares_one_context_and_cannot_scroll(monkeypatch):
    from rastro.adapters.scrapling_stealth import ScraplingStealth

    monkeypatch.setattr(ScraplingStealth, "__init__", lambda self: None)
    stealth = ScraplingStealth()
    stealth.session_class = Manager
    async with stealth.session((TARGET,), policy=POLICY) as session:
        await session.render(TARGET, policy=POLICY, max_bytes=1000)
        result = await session.render(TARGET, policy=POLICY, max_bytes=1000)
        with pytest.raises(AcquisitionError) as error:
            await session.scroll(policy=POLICY, max_bytes=1000, settle_ms=100)
    assert result.body == "<p>ok</p>"
    assert (Manager.engine.entered, Manager.engine.exited) == (1, 1)
    assert Manager.engine.urls == [TARGET.url] * 2
    assert error.value.code == "scroll_unsupported"
