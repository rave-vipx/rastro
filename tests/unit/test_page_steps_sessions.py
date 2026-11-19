"""Page steps through the real browser session classes, with fake drivers underneath.

Both sessions must match the ``PageStepRunner`` port: ``run_steps(steps, *, policy,
max_bytes)``, the guard armed, ``perform_steps`` given the steps' own budget, and a
``PageStepError`` passed up unchanged so the failed step keeps its position.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from rastro.adapters.playwright_browser import NetworkGuard
from rastro.adapters.playwright_session import PlaywrightSession
from rastro.adapters.scrapling_stealth import StealthSession
from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.browser_policy import BROWSER_REGION
from rastro.application.browser_sessions import source_session
from rastro.application.page_steps import PageStepError, steps_budget_ms
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    HttpResponse,
    PinnedTarget,
)
from rastro.domain.models import Outcome, Source
from rastro.domain.page_steps import PageSteps
from rastro.ports.engine import PageStepRunner

TARGET = PinnedTarget("https://shop.example/list", "shop.example", 443, "93.184.216.34")
POLICY = AcquisitionPolicy(allowed_hosts=("shop.example",), render="browser")
STEPS = TypeAdapter(PageSteps)
CLICK = STEPS.validate_python([{"action": "click", "selector": "button.more"}])
MISSING = STEPS.validate_python([{"action": "click", "selector": "button.gone"}])


class Timeout(Exception):
    pass


Timeout.__name__ = "TimeoutError"


class Page:
    def __init__(self):
        self.url, self.expanded, self.timeouts = "", False, []

    async def goto(self, url, **kwargs):
        self.url = url
        return SimpleNamespace(status=200)

    async def wait_for_selector(self, selector, *, state, timeout):
        self.timeouts.append(timeout)
        if selector != "css=button.more":
            raise Timeout

    async def click(self, selector, *, timeout):
        self.expanded = True

    async def content(self):
        return "<p>more</p>" if self.expanded else "<p>first</p>"


class Context:
    def __init__(self):
        self.page = Page()

    async def new_page(self):
        return self.page


@pytest.fixture
def guard(monkeypatch):
    async def setup(self, page):
        return None

    monkeypatch.setattr(NetworkGuard, "setup", setup)
    return NetworkGuard(POLICY)


@pytest.mark.asyncio
async def test_playwright_session_runs_steps_on_its_open_page(guard):
    context = Context()
    session = PlaywrightSession(context, guard)
    assert isinstance(session, PageStepRunner)
    with pytest.raises(AcquisitionError):  # no page rendered yet: nothing to act on
        await session.run_steps(CLICK, policy=POLICY, max_bytes=10_000)
    await session.render(TARGET, policy=POLICY, max_bytes=10_000)
    result = await session.run_steps(CLICK, policy=POLICY, max_bytes=10_000)
    assert result.body == "<p>more</p>" and result.status == Outcome.SUCCESS
    assert not guard.active  # idle again between steps


@pytest.mark.asyncio
async def test_playwright_session_passes_the_failed_step_up_unchanged(guard):
    session = PlaywrightSession(Context(), guard)
    await session.render(TARGET, policy=POLICY, max_bytes=10_000)
    with pytest.raises(PageStepError) as caught:
        await session.run_steps(MISSING, policy=POLICY, max_bytes=10_000)
    assert (caught.value.code, caught.value.step) == ("step_selector_missing", 1)


class Engine:
    def __init__(self):
        self.page, self.fetched = Page(), []

    async def fetch(self, url, *, page_action):
        self.fetched.append(url)
        self.page.url, self.page.expanded = url, False
        await page_action(self.page)
        body = await self.page.content()
        return SimpleNamespace(url=url, html_content=body, status=200)


@pytest.mark.asyncio
async def test_stealth_session_reloads_its_page_and_runs_the_steps(guard):
    engine = Engine()
    session = StealthSession(engine, guard)
    assert isinstance(session, PageStepRunner)
    with pytest.raises(AcquisitionError):
        await session.run_steps(CLICK, policy=POLICY, max_bytes=10_000)
    first = await session.render(TARGET, policy=POLICY, max_bytes=10_000)
    after = await session.run_steps(CLICK, policy=POLICY, max_bytes=10_000)
    assert (first.body, after.body) == ("<p>first</p>", "<p>more</p>")
    assert engine.fetched == [TARGET.url, TARGET.url]
    assert max(engine.page.timeouts) <= steps_budget_ms(CLICK, POLICY)
    with pytest.raises(PageStepError) as caught:
        await session.run_steps(MISSING, policy=POLICY, max_bytes=10_000)
    assert caught.value.code == "step_selector_missing"


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    async def get(self, target, **kwargs):
        return HttpResponse(404, {}, b"", 0)


class Renderer:
    """Opens a real ``PlaywrightSession`` over a fake context; records the region it saw."""

    def __init__(self, guard):
        self.guard, self.regions = guard, []

    @asynccontextmanager
    async def session(self, targets, *, policy):
        self.regions.append(BROWSER_REGION.get())
        self.guard.begin(policy)
        yield PlaywrightSession(Context(), self.guard)

    async def render(self, target, *, policy, max_bytes, targets):  # pragma: no cover
        raise AssertionError("the source session renders")


@pytest.mark.asyncio
async def test_browser_acquirer_runs_steps_through_the_real_session(guard):
    renderer = Renderer(guard)
    web = BrowserAcquirer(
        POLICY.model_copy(update={"min_interval_seconds": 0}),
        renderer=renderer,
        transport=Transport(),
        resolver=Resolver(),
    )
    source = Source(url=TARGET.url, locale="es-ES", timezone="Europe/Madrid")
    async with source_session():
        first = await web.acquire(source, max_bytes=10_000)
        after = await web.run_steps(Source(url=first.url), CLICK, max_bytes=10_000)
    assert (first.body, after.body) == ("<p>first</p>", "<p>more</p>")
    assert renderer.regions == [("es-ES", "Europe/Madrid")]  # the job's [region] reached it
    assert BROWSER_REGION.get() is None  # and does not leak past the render
