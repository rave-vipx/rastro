"""A spider runs inside one browser context per start source.

The renderer is a fake: no real browser, no network.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.browser_sessions import source_session
from rastro.application.jobs import run_job
from rastro.domain.acquisition import AcquisitionResult, HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError, Outcome, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    async def get(self, target, **kwargs):
        return HttpResponse(404, {}, b"", 0)  # robots.txt: none


def item(name, *hrefs, more=()):
    anchors = "".join(f'<a class="item" href="{href}">x</a>' for href in hrefs)
    names = "".join(f"<article><h2>{n}</h2></article>" for n in (name, *more))
    return f"<html><body>{names}{anchors}</body></html>"


SITE = {
    "/list": item("Root", "/a", "/b", "/c"),
    "/a": item("A"),
    "/b": item("B"),
    "/c": item("C"),
}


class Session:
    """Fails the test when two renders or scrolls overlap: one page serves one step at a time."""

    def __init__(self, owner):
        self.owner, self.url = owner, ""

    async def _step(self, kind, url, body):
        owner = self.owner
        owner.live += 1
        owner.peak = max(owner.peak, owner.live)
        owner.events.append((kind, url))
        try:
            await asyncio.sleep(0.01)
        finally:
            owner.live -= 1
        return AcquisitionResult(url, body, Outcome.SUCCESS, None, http_status=200, requests=1)

    async def render(self, target, *, policy, max_bytes):
        self.url = target.url
        return await self._step("render", target.url, self.owner.pages[urlsplit(target.url).path])

    async def scroll(self, *, policy, max_bytes, settle_ms):
        steps = self.owner.scroll_bodies
        index = min(self.owner.scrolls, len(steps) - 1)
        self.owner.scrolls += 1
        return await self._step("scroll", self.url, steps[index])


class FakeBrowser:
    def __init__(self, pages, scroll_bodies=()):
        self.pages, self.scroll_bodies = pages, list(scroll_bodies)
        self.opened = self.closed = self.scrolls = self.legacy = self.live = self.peak = 0
        self.events = []

    @asynccontextmanager
    async def session(self, targets, *, policy):
        self.opened += 1
        try:
            yield Session(self)
        finally:
            self.closed += 1

    async def render(self, target, *, policy, max_bytes, targets):
        self.legacy += 1
        body = self.pages[urlsplit(target.url).path]
        return AcquisitionResult(target.url, body, Outcome.SUCCESS, None, http_status=200)


def job(*, spider=None, scroll=None, render="browser", budget=30):
    data = {
        "format_version": 1,
        "sources": [{"url": "https://shop.test/list"}],
        "fields": [{"name": "name", "type": "string"}],
        "recipe": {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}},
        "spider": {"mode": "spider", "links": "a.item::attr(href)", **(spider or {})},
        "acquisition": {
            "allowed_hosts": ["shop.test"],
            "max_requests": budget,
            "min_interval_seconds": 0,
            "max_retries": 0,
            "max_concurrency": 4,
            "render": render,
        },
    }
    if scroll is not None:
        data["scroll"] = scroll
    return Job.model_validate(data)


def run_browser(spec, browser):
    acquirer = BrowserAcquirer(
        spec.acquisition, renderer=browser, transport=Transport(), resolver=Resolver()
    )
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd())


def test_a_browser_spider_opens_one_context_and_closes_it():
    browser = FakeBrowser(SITE)
    result = run_browser(job(), browser).results[0]
    assert [r.name for r in result.items] == ["Root", "A", "B", "C"]
    assert (browser.opened, browser.closed, browser.legacy) == (1, 1, 0)


def test_browser_spider_pages_never_render_concurrently():
    browser = FakeBrowser(SITE)
    result = run_browser(job(), browser).results[0]
    assert len(result.items) == 4
    assert browser.peak == 1  # max_concurrency is 4, one page serves one step at a time


class FakeAcquisition:
    """An acquisition port with no browser: concurrency is only bounded by max_concurrency."""

    def __init__(self, pages):
        self.pages, self.live, self.peak = pages, 0, 0

    async def acquire(self, source, *, max_bytes, representation="html"):
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.live -= 1
        body = self.pages[urlsplit(source.url).path]
        return AcquisitionResult(
            source.url, body, Outcome.SUCCESS, None, http_status=200, requests=1
        )


def test_an_http_spider_stays_concurrent():
    spec = job(render="http")
    acquisition = FakeAcquisition(SITE)
    result = run_job(spec, acquisition=acquisition, base_dir=Path.cwd()).results[0]
    assert len(result.items) == 4
    assert acquisition.peak > 1


def test_scroll_steps_spend_the_spiders_page_cap():
    # start page = 1 of 3; one scroll step = 2 of 3; so exactly one more page is fetched
    browser = FakeBrowser(SITE, [item("Root", "/a", "/b", "/c"), item("Root", "/a", "/b", "/c")])
    spec = job(spider={"max_pages": 3}, scroll={"max_steps": 1})
    result = run_browser(spec, browser).results[0]
    renders = [url for kind, url in browser.events if kind == "render"]
    assert len(renders) == 2, renders
    assert result.pagination_stop == "spider_page_limit"
    assert result.status == Outcome.PARTIAL_SUCCESS


def test_two_scroll_steps_on_the_start_page_use_up_a_cap_of_three():
    browser = FakeBrowser(
        SITE,
        [
            item("Root", "/a", "/b", "/c", more=("S1",)),
            item("Root", "/a", "/b", "/c", more=("S1", "S2")),
            item("Root", "/a", "/b", "/c", more=("S1", "S2")),
        ],
    )
    spec = job(spider={"max_pages": 3}, scroll={"max_steps": 5})
    result = run_browser(spec, browser).results[0]
    kinds = [kind for kind, _ in browser.events]
    assert kinds.count("render") == 1  # no page is left for any link
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert any(i.code == "scroll_budget" for i in result.issues)
    assert [r.name for r in result.items] == ["Root", "S1", "S2"]  # the cap keeps the records
    assert result.spider.pages[1].outcome == "page_limit"


def test_the_request_budget_is_shared_by_pages_and_scroll_steps():
    browser = FakeBrowser(
        SITE,
        [
            item("Root", "/a", "/b", "/c", more=tuple(f"S{n}" for n in range(k)))
            for k in range(1, 10)
        ],
    )
    spec = job(spider={"max_pages": 50}, scroll={"max_steps": 30}, budget=4)
    result = run_browser(spec, browser).results[0]
    spent = sum(1 for kind, _ in browser.events if kind in {"render", "scroll"})
    assert spent <= 4
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.records  # a cap keeps the records collected so far


def test_only_browser_modes_report_a_shared_page():
    from rastro.browser import _JobAcquisition
    from rastro.ports.browser import BrowserRendered

    routed = _JobAcquisition({"https://shop.test/list": "browser"}, {}, default="http")
    assert isinstance(routed, BrowserRendered)
    assert routed.renders_in_browser("https://shop.test/list")
    assert not routed.renders_in_browser("https://other.test/")


OTHER = {
    "/list": item("Root", "https://other.test/x", "https://other.test/y"),
    "/x": item("X"),
    "/y": item("Y"),
}


def cross_host_job(*, max_pages=10):
    spec = job(spider={"same_host": False, "max_pages": max_pages}, scroll={"max_steps": 1})
    return spec.model_copy(
        update={
            "acquisition": spec.acquisition.model_copy(
                update={"allowed_hosts": ("shop.test", "other.test")}
            )
        }
    )


def test_a_scroll_never_runs_on_a_page_rendered_outside_the_session():
    leaked = item("Root", "https://other.test/x", "https://other.test/y", more=("LEAKED",))
    browser = FakeBrowser(OTHER, [leaked])
    result = run_browser(cross_host_job(), browser).results[0]
    names = [r.name for r in result.items]
    assert names.count("LEAKED") == 1 and names.count("Root") == 1  # no copy for other.test
    assert sorted(names) == ["LEAKED", "Root", "X", "Y"]
    scrolled = [url for kind, url in browser.events if kind == "scroll"]
    assert scrolled == ["https://shop.test/list"]  # nothing scrolled for the other host's pages
    assert any(i.code == "scroll_step_failed" for i in result.issues)
    assert not any("other.test" in (i.code or "") for i in result.issues)  # content-free codes


def test_a_refused_scroll_step_gives_its_page_back_to_the_page_cap():
    # list 1, its step 2, x 3, y 4, x's refused step 5: with the page given back, y's step
    # is refused for the same reason instead of being starved by the cap.
    browser = FakeBrowser(OTHER, [item("Root", "https://other.test/x", "https://other.test/y")])
    result = run_browser(cross_host_job(max_pages=5), browser).results[0]
    assert sorted(r.name for r in result.items) == ["Root", "X", "Y"]
    assert not any(i.code == "scroll_budget" for i in result.issues)


@pytest.mark.asyncio
async def test_a_scroll_step_is_refused_unless_the_page_is_the_one_just_rendered():
    browser = FakeBrowser(SITE, [SITE["/list"]])
    acquirer = BrowserAcquirer(
        job().acquisition, renderer=browser, transport=Transport(), resolver=Resolver()
    )
    async with source_session():
        await acquirer.acquire(Source(url="https://shop.test/a"), max_bytes=100_000)
        await acquirer.scroll(Source(url="https://shop.test/a"), settle_ms=0, max_bytes=100_000)
        await acquirer.acquire(Source(url="https://shop.test/b"), max_bytes=100_000)
        with pytest.raises(ConfigurationError):  # the session moved on to /b
            await acquirer.scroll(Source(url="https://shop.test/a"), settle_ms=0, max_bytes=100_000)
    assert browser.scrolls == 1


def test_a_crawl_is_sequential_when_any_page_may_render_in_a_browser():
    from rastro.browser import _JobAcquisition

    http_start = _JobAcquisition({"https://shop.test/list": "http"}, {}, default="browser")
    assert not http_start.renders_in_browser("https://shop.test/list")
    assert http_start.may_render_in_browser("https://shop.test/list")
    all_http = _JobAcquisition({"https://shop.test/list": "http"}, {}, default="http")
    assert not all_http.may_render_in_browser("https://shop.test/list")
    declared = _JobAcquisition(
        {"https://shop.test/list": "http", "https://other.test/": "browser"}, {}, default="http"
    )
    assert declared.may_render_in_browser("https://shop.test/list")  # a link may reach other.test


def test_an_http_start_with_a_browser_default_crawls_one_page_at_a_time():
    from rastro.browser import _JobAcquisition

    live = {"now": 0, "peak": 0}

    class Http:
        async def acquire(self, source, *, max_bytes, representation="html"):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.01)
            live["now"] -= 1
            body = SITE[urlsplit(source.url).path]
            return AcquisitionResult(source.url, body, Outcome.SUCCESS, None, http_status=200)

    routed = _JobAcquisition(
        {"https://shop.test/list": "http"}, {"http": Http()}, default="browser"
    )
    spec = Job.model_validate(
        {
            **job(render="browser").model_dump(mode="json", exclude_none=True),
            "sources": [{"url": "https://shop.test/list", "render": "http"}],
        }
    )
    result = run_job(spec, acquisition=routed, base_dir=Path.cwd()).results[0]
    assert len(result.items) == 4
    assert live["peak"] == 1
