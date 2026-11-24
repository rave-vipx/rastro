"""Failures after browser transfers keep their count; no unsafe reuse."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from rastro.application.acquisition_state import PAGINATION_SCOPE
from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.browser_sessions import source_session
from rastro.application.scroll import scroll_scope
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
)
from rastro.domain.models import Outcome, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

POLICY = AcquisitionPolicy(
    allowed_hosts=("agenda.test", "other.test"), min_interval_seconds=0, max_requests=30
)


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    async def get(self, target, **kwargs):
        return HttpResponse(404, {}, b"", 0)


def _guard(requests=4):
    from rastro.adapters.playwright_browser import NetworkGuard

    guard = NetworkGuard(AcquisitionPolicy(allowed_hosts=("example.com",)))
    guard.requests = requests
    return guard


@pytest.mark.parametrize(
    ("body", "max_bytes", "code"),
    [
        ("<p>cf-turnstile</p>", 1000, "challenge_unsolved"),
        ("<p>" + "x" * 50 + "</p>", 10, "response_too_large"),
    ],
)
def test_guard_failures_keep_the_transfers_the_page_made(body, max_bytes, code):
    with pytest.raises(AcquisitionError, match=code) as error:
        _guard().result("https://example.com/", body, 200, max_bytes)
    assert error.value.requests == 4


class Session:
    def __init__(self, owner):
        self.owner, self.url = owner, ""

    async def render(self, target, *, policy, max_bytes):
        self.owner.events.append(("render", target.url))
        if self.owner.hang_render:
            await asyncio.sleep(5)
        if self.owner.render_error:
            raise self.owner.render_error
        self.url = target.url
        return AcquisitionResult(
            target.url, "<p>ok</p>", Outcome.SUCCESS, None, http_status=200, requests=1
        )

    async def scroll(self, *, policy, max_bytes, settle_ms):
        self.owner.granted.append(policy.max_requests)
        if self.owner.hang_scroll:
            await asyncio.sleep(5)
        if self.owner.scroll_error:
            raise self.owner.scroll_error
        return self.owner.scroll_result(self.url)


class Browser:
    def __init__(self):
        self.opened = self.closed = 0
        self.events, self.render_error, self.scroll_error = [], None, None
        self.granted, self.hang_render, self.hang_scroll = [], False, False
        self.scroll_result = lambda url: AcquisitionResult(
            url, "<p>ok</p>", Outcome.SUCCESS, None, http_status=200, requests=4
        )

    @asynccontextmanager
    async def session(self, targets, *, policy):
        self.opened += 1
        try:
            yield Session(self)
        finally:
            self.closed += 1

    async def render(self, target, *, policy, max_bytes, targets):
        raise AssertionError("the per-page path is not used here")


def _acquirer(browser):
    return BrowserAcquirer(POLICY, renderer=browser, transport=Transport(), resolver=Resolver())


@pytest.mark.parametrize(
    "outcome",
    [
        lambda url: AcquisitionResult(
            "https://agenda.test/forbidden", "<p>x</p>", Outcome.SUCCESS, None, requests=4
        ),
        lambda url: AcquisitionResult(
            url, None, Outcome.ACCESS_RESTRICTION, "challenge_unsolved", requests=4
        ),
        lambda url: AcquisitionResult(url, "", Outcome.SUCCESS, None, requests=4),
        lambda url: AcquisitionResult(
            url, "<p>" + "x" * 99 + "</p>", Outcome.SUCCESS, None, requests=4
        ),
    ],
    ids=["navigated", "failed-status", "empty", "too-large"],
)
@pytest.mark.asyncio
async def test_every_refused_scroll_step_charges_what_it_transferred(outcome):
    browser = Browser()
    browser.scroll_result = outcome
    client = _acquirer(browser)
    source = Source(url="https://agenda.test/list")
    async with source_session():
        await client.acquire(source, max_bytes=50)  # 9 bytes: fits
        step = await client.scroll(source, settle_ms=10, max_bytes=50)
    assert step.status != Outcome.SUCCESS
    assert step.requests == 4


@pytest.mark.asyncio
async def test_a_render_that_raises_discards_its_context():
    """The final host of a failed render is unknown: the next page opens a fresh context."""
    browser = Browser()
    client = _acquirer(browser)
    async with source_session():
        browser.render_error = AcquisitionError("operation_timeout", Outcome.TRANSIENT_FAILURE)
        failed = await client.acquire(Source(url="https://agenda.test/start"), max_bytes=1000)
        assert failed.status == Outcome.TRANSIENT_FAILURE
        assert (browser.opened, browser.closed) == (1, 1)  # discarded at once
        browser.render_error = None
        await client.acquire(Source(url="https://agenda.test/next"), max_bytes=1000)
        assert (browser.opened, browser.closed) == (2, 1)  # a fresh one, not the failed one


@pytest.mark.asyncio
async def test_a_scroll_that_raises_discards_its_context():
    browser = Browser()
    client = _acquirer(browser)
    source = Source(url="https://agenda.test/list")
    async with source_session():
        await client.acquire(source, max_bytes=1000)
        browser.scroll_error = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await client.scroll(source, settle_ms=10, max_bytes=1000)
        assert (browser.opened, browser.closed) == (1, 1)
        await client.acquire(Source(url="https://agenda.test/next"), max_bytes=1000)
        assert browser.opened == 2


def _quick_timeout(browser):
    policy = POLICY.model_copy(update={"request_timeout_seconds": 0.05})
    return BrowserAcquirer(policy, renderer=browser, transport=Transport(), resolver=Resolver())


@pytest.mark.asyncio
async def test_a_render_that_times_out_spends_its_whole_allowance():
    """A timeout loses the browser's own count: never undercount, charge what was granted."""
    browser, source = Browser(), Source(url="https://agenda.test/slow")
    browser.hang_render = True
    async with source_session():
        with scroll_scope(source, POLICY):
            scope = PAGINATION_SCOPE.get()
            result = await _quick_timeout(browser).acquire(source, max_bytes=1000)
            assert result.status == Outcome.TRANSIENT_FAILURE
            assert scope is not None and scope.remaining == 0  # nothing left for a later page


@pytest.mark.asyncio
async def test_a_scroll_step_that_times_out_spends_its_whole_allowance():
    browser, source = Browser(), Source(url="https://agenda.test/list")
    client = _quick_timeout(browser)
    async with source_session():
        with scroll_scope(source, POLICY):
            scope = PAGINATION_SCOPE.get()
            await client.acquire(source, max_bytes=1000)
            assert scope is not None
            before, browser.hang_scroll = scope.remaining, True
            step = await client.scroll(source, settle_ms=10, max_bytes=1000)
    assert step.issue_code == "operation_timeout"
    assert step.requests == before  # the whole remaining budget, which is all the step was given
    assert browser.granted == [before]  # and the step was never granted more than was left
