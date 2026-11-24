"""The browser path keeps every boundary of the source.

Fake renderers only: no real browser, no network. The budget fake mimics NetworkGuard: it lets
at most ``policy.max_requests`` transfers through and reports the ones it let through.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope
from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.browser_sessions import source_session
from rastro.application.jobs import run_job
from rastro.domain.acquisition import AcquisitionError, AcquisitionResult, HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError, Outcome, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    """robots.txt: none by default; every call is one transfer."""

    def __init__(self, robots=None):
        self.calls, self.robots = 0, robots

    async def get(self, target, **kwargs):
        self.calls += 1
        if self.robots is not None:
            body = self.robots.encode()
            return HttpResponse(200, {"content-type": "text/plain"}, body, len(body))
        return HttpResponse(404, {}, b"", 0)


def doc(*names, links=()):
    anchors = "".join(f'<a class="item" href="{href}">x</a>' for href in links)
    return (
        "<html><body>"
        + "".join(f"<article><h2>{n}</h2></article>" for n in names)
        + anchors
        + ("</body></html>")
    )


class Session:
    def __init__(self, owner):
        self.owner, self.url = owner, ""
        self.policies = []

    def final(self, url):
        return self.owner.redirects.get(url, url)

    async def render(self, target, *, policy, max_bytes):
        owner = self.owner
        owner.log.append(("render", id(self), target.url))
        self.url = self.final(target.url)
        sent = 1  # the document
        for _ in range(owner.xhrs):
            if sent >= policy.max_requests:
                owner.transfers += sent
                raise AcquisitionError("request_limit", Outcome.PERMANENT_FAILURE, requests=sent)
            sent += 1
        owner.transfers += sent
        body = owner.pages[urlsplit(self.url).path]
        return AcquisitionResult(
            self.url, body, Outcome.SUCCESS, None, http_status=200, requests=sent
        )

    async def scroll(self, *, policy, max_bytes, settle_ms):
        self.policies.append(policy)
        owner = self.owner
        url = owner.scroll_url or self.url
        body = owner.pages[urlsplit(url).path]
        return AcquisitionResult(url, body, Outcome.SUCCESS, None, http_status=200, requests=1)


class Browser:
    def __init__(self, pages, *, xhrs=0, redirects=None, scroll_url=None):
        self.pages, self.xhrs, self.redirects = pages, xhrs, redirects or {}
        self.scroll_url = scroll_url
        self.transfers = self.opened = self.closed = 0
        self.log, self.sessions = [], []

    @asynccontextmanager
    async def session(self, targets, *, policy):
        self.opened += 1
        session = Session(self)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.closed += 1


def spec(
    *, hosts=("shop.test",), budget=30, allow_http=False, url="https://shop.test/list", **extra
):
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": url}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {
                "strategy": "css",
                "record_selector": "article",
                "fields": {"name": "h2::text"},
            },
            "spider": {"mode": "spider", "links": "a.item::attr(href)", "same_host": False},
            "acquisition": {
                "allowed_hosts": list(hosts),
                "max_requests": budget,
                "min_interval_seconds": 0,
                "max_retries": 0,
                "max_concurrency": 4,
                "render": "browser",
                "allow_http": allow_http,
            },
            **extra,
        }
    )


def acquirer(job, browser, transport=None):
    return BrowserAcquirer(
        job.acquisition, renderer=browser, transport=transport or Transport(), resolver=Resolver()
    )


# 1. The shared request budget bounds what a page's own requests may add ------------------


def test_a_browser_spider_never_makes_more_transfers_than_the_budget():
    browser = Browser({"/list": doc("Root", links=["/a"]), "/a": doc("A")}, xhrs=2)
    transport = Transport()
    result = run_job(
        spec(budget=6),
        acquisition=acquirer(spec(budget=6), browser, transport),
        base_dir=Path.cwd(),
    ).results[0]
    assert transport.calls + browser.transfers <= 6, (transport.calls, browser.transfers)
    assert [r.name for r in result.items] == ["Root"]  # what was accepted stays
    assert result.status == Outcome.PARTIAL_SUCCESS


@pytest.mark.asyncio
async def test_transfers_of_a_render_that_fails_are_still_charged():
    class Failing(Browser):
        @asynccontextmanager
        async def session(self, targets, *, policy):
            class Boom(Session):
                async def render(self, target, *, policy, max_bytes):
                    raise AcquisitionError(
                        "destination_denied", Outcome.ACCESS_RESTRICTION, requests=4
                    )

            yield Boom(self)

    job = spec()
    scope = PaginationScope("shop.test", True, 10, 10)
    token = PAGINATION_SCOPE.set(scope)
    try:
        async with source_session():
            result = await acquirer(job, Failing({})).acquire(
                Source(url="https://shop.test/list"), max_bytes=100_000
            )
    finally:
        PAGINATION_SCOPE.reset(token)
    # robots 1 + navigation 1 + the 3 further transfers the guard let through
    assert result.requests == 5
    assert scope.remaining == 10 - 5


# 2. A scroll step keeps the HTTPS-only constraint -----------------------------------------


@pytest.mark.asyncio
async def test_scroll_after_an_https_page_never_allows_http_again():
    browser = Browser({"/list": doc("Root")})
    job = spec(allow_http=True)
    web = acquirer(job, browser)
    async with source_session():
        await web.acquire(Source(url="https://shop.test/list"), max_bytes=100_000)
        await web.scroll(Source(url="https://shop.test/list"), settle_ms=0, max_bytes=100_000)
    assert [p.allow_http for p in browser.sessions[0].policies] == [False]


@pytest.mark.asyncio
async def test_scroll_on_an_http_page_keeps_what_the_operator_allowed():
    browser = Browser({"/list": doc("Root")})
    job = spec(allow_http=True, url="http://shop.test/list")
    web = acquirer(job, browser)
    async with source_session():
        await web.acquire(Source(url="http://shop.test/list"), max_bytes=100_000)
        await web.scroll(Source(url="http://shop.test/list"), settle_ms=0, max_bytes=100_000)
    assert [p.allow_http for p in browser.sessions[0].policies] == [True]


# 3. A scroll step that lands on another document is refused -------------------------------


def test_a_scroll_that_navigates_to_a_robots_disallowed_page_keeps_only_earlier_records():
    pages = {"/one": doc("One"), "/forbidden": doc("One", "Forbidden")}
    browser = Browser(pages, scroll_url="https://shop.test/forbidden")
    job = spec(url="https://shop.test/one", scroll={"max_steps": 2})
    transport = Transport(robots="User-agent: *\nDisallow: /forbidden\n")
    result = run_job(
        job, acquisition=acquirer(job, browser, transport), base_dir=Path.cwd()
    ).results[0]
    assert [r.name for r in result.items] == ["One"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [i.code for i in result.issues if i.code.startswith("scroll_")] == ["scroll_step_failed"]


@pytest.mark.asyncio
async def test_a_scroll_step_whose_document_url_changed_is_a_step_failure():
    browser = Browser(
        {"/list": doc("Root"), "/other": doc("Other")}, scroll_url="https://shop.test/other"
    )
    web = acquirer(spec(), browser)
    async with source_session():
        await web.acquire(Source(url="https://shop.test/list"), max_bytes=100_000)
        step = await web.scroll(
            Source(url="https://shop.test/list"), settle_ms=0, max_bytes=100_000
        )
        with pytest.raises(ConfigurationError):  # the session no longer shows the page
            await web.scroll(Source(url="https://shop.test/list"), settle_ms=0, max_bytes=100_000)
    assert (step.status, step.issue_code, step.body) == (
        Outcome.PERMANENT_FAILURE,
        "scroll_navigated",
        None,
    )
    assert len(browser.sessions[0].policies) == 1


# 4. A context that held another host's document is never reused ---------------------------


@pytest.mark.asyncio
async def test_a_context_that_followed_a_redirect_to_another_host_is_not_reused():
    pages = {"/start": doc("B landed"), "/next": doc("A page")}
    browser = Browser(pages, redirects={"https://a.test/start": "https://b.test/start"})
    job = spec(hosts=("a.test", "b.test"), url="https://a.test/start")
    web = acquirer(job, browser)
    async with source_session():
        first = await web.acquire(Source(url="https://a.test/start"), max_bytes=100_000)
        second = await web.acquire(Source(url="https://a.test/next"), max_bytes=100_000)
    assert first.url == "https://b.test/start" and second.url == "https://a.test/next"
    rendered = [(kind, sid, url) for kind, sid, url in browser.log if kind == "render"]
    assert rendered[0][1] != rendered[1][1]  # B's context never rendered A's next page
    assert (browser.opened, browser.closed) == (2, 2)


@pytest.mark.asyncio
async def test_a_same_host_page_keeps_reusing_its_context():
    browser = Browser({"/one": doc("1"), "/two": doc("2")})
    web = acquirer(spec(), browser)
    async with source_session():
        await web.acquire(Source(url="https://shop.test/one"), max_bytes=100_000)
        await web.acquire(Source(url="https://shop.test/two"), max_bytes=100_000)
    assert (browser.opened, browser.closed) == (1, 1)
