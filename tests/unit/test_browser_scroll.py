"""N2, N9, SEC-001/005/008: one browser context per source, and bounded scroll (fake browser)."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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
    async def get(self, target, **kwargs):
        return HttpResponse(404, {}, b"", 0)  # robots.txt: none


def page(*names, next=None):
    html = "<html><body>" + "".join(f"<article><h2>{name}</h2></article>" for name in names)
    return html + (f'<a rel="next" href="{next}">Next</a>' if next else "") + "</body></html>"


class Session:
    def __init__(self, owner):
        self.owner, self.url = owner, ""

    async def render(self, target, *, policy, max_bytes):
        self.url = target.url
        self.owner.events.append(("render", target.url))
        body = self.owner.pages[urlsplit(target.url).path]
        return AcquisitionResult(
            target.url, body, Outcome.SUCCESS, None, http_status=200, requests=1
        )

    async def scroll(self, *, policy, max_bytes, settle_ms):
        steps = self.owner.scroll_bodies
        index = min(self.owner.scrolls, len(steps) - 1)
        self.owner.scrolls += 1
        self.owner.settles.append(settle_ms)
        self.owner.events.append(("scroll", self.url))
        return AcquisitionResult(
            self.url, steps[index], Outcome.SUCCESS, None, http_status=200, requests=1
        )


class FakeBrowser:
    """A renderer that implements both the per-page and the session protocol."""

    def __init__(self, pages, scroll_bodies=()):
        self.pages, self.scroll_bodies = pages, list(scroll_bodies)
        self.opened = self.closed = self.scrolls = self.legacy = 0
        self.events, self.settles = [], []

    @asynccontextmanager
    async def session(self, targets, *, policy):
        self.opened += 1
        self.events.append(("open", targets[0].host))
        try:
            yield Session(self)
        finally:
            self.closed += 1
            self.events.append(("close", targets[0].host))

    async def render(self, target, *, policy, max_bytes, targets):
        self.legacy += 1
        body = self.pages[urlsplit(target.url).path]
        return AcquisitionResult(target.url, body, Outcome.SUCCESS, None, http_status=200)


class LegacyRenderer:
    """Only the per-page protocol: it keeps today's behavior."""

    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    async def render(self, target, *, policy, max_bytes, targets):
        self.calls += 1
        body = self.pages[urlsplit(target.url).path]
        return AcquisitionResult(target.url, body, Outcome.SUCCESS, None, http_status=200)


def job(*, paths=("/one",), scroll=None, pagination=False, budget=30, render="browser"):
    recipe = {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}}
    if pagination:
        recipe["pagination"] = {"next": "a[rel=next]::attr(href)", "max_pages": 5}
    data = {
        "format_version": 1,
        "sources": [{"url": f"https://agenda.test{path}"} for path in paths],
        "fields": [{"name": "name", "type": "string"}],
        "recipe": recipe,
        "acquisition": {
            "allowed_hosts": ["agenda.test", "other.test"],
            "max_requests": budget,
            "min_interval_seconds": 0,
            "max_retries": 0,
            "render": render,
        },
    }
    if scroll is not None:
        data["scroll"] = scroll
    return Job.model_validate(data)


def acquirer(spec, renderer):
    return BrowserAcquirer(
        spec.acquisition, renderer=renderer, transport=Transport(), resolver=Resolver()
    )


def run(spec, renderer):
    return run_job(spec, acquisition=acquirer(spec, renderer), base_dir=Path.cwd())


def test_one_context_serves_every_page_of_a_paginated_source():
    browser = FakeBrowser(
        {
            "/one": page("A", next="/two"),
            "/two": page("B", next="/three"),
            "/three": page("C"),
        }
    )
    result = run(job(pagination=True), browser).results[0]
    assert [record.name for record in result.items] == ["A", "B", "C"]
    assert result.pagination_stop == "no_next"
    assert (browser.opened, browser.closed, browser.legacy) == (1, 1, 0)
    kinds = [kind for kind, _ in browser.events]
    assert kinds == ["open", "render", "render", "render", "close"]


def test_each_source_gets_its_own_context():
    browser = FakeBrowser({"/one": page("A"), "/two": page("B")})
    report = run(job(paths=("/one", "/two")), browser)
    assert [r.items[0].name for r in report.results] == ["A", "B"]
    assert (browser.opened, browser.closed) == (2, 2)


@pytest.mark.asyncio
async def test_a_context_is_never_shared_across_hosts():
    spec = job()
    browser = FakeBrowser({"/": page("A")})
    client = acquirer(spec, browser)
    async with source_session():
        await client.acquire(Source(url="https://agenda.test/"), max_bytes=10_000)
        await client.acquire(Source(url="https://other.test/"), max_bytes=10_000)
        await client.acquire(Source(url="https://agenda.test/"), max_bytes=10_000)
    assert browser.opened == 1  # agenda.test twice
    assert browser.legacy == 1  # other.test: a fresh context, never the agenda.test session
    assert browser.closed == 1


@pytest.mark.asyncio
async def test_without_a_source_scope_every_page_stays_fresh():
    browser = FakeBrowser({"/": page("A")})
    client = acquirer(job(), browser)
    for _ in range(2):
        await client.acquire(Source(url="https://agenda.test/"), max_bytes=10_000)
    assert (browser.opened, browser.legacy) == (0, 2)


def test_a_renderer_without_sessions_keeps_the_per_page_behavior():
    renderer = LegacyRenderer({"/one": page("A", next="/two"), "/two": page("B")})
    result = run(job(pagination=True), renderer).results[0]
    assert [record.name for record in result.items] == ["A", "B"]
    assert renderer.calls == 2


@pytest.mark.asyncio
async def test_an_error_inside_the_source_still_closes_the_context():
    browser = FakeBrowser({"/": page("A")})
    client = acquirer(job(), browser)
    with pytest.raises(RuntimeError):
        async with source_session():
            await client.acquire(Source(url="https://agenda.test/"), max_bytes=10_000)
            raise RuntimeError("interrupted")
    assert browser.opened == browser.closed == 1


def scroll_job(**scroll):
    return job(scroll={"settle_ms": 150, **scroll})


def test_scroll_stops_on_a_step_with_no_new_records():
    browser = FakeBrowser(
        {"/one": page("A", "B")},
        [page("A", "B", "C"), page("A", "B", "C"), page("A", "B", "C", "D")],
    )
    result = run(scroll_job(max_steps=10), browser).results[0]
    assert [record.name for record in result.items] == ["A", "B", "C"]
    assert result.status == Outcome.SUCCESS
    assert not result.issues
    assert browser.scrolls == 2  # the second step found nothing new
    assert browser.settles == [150, 150]
    assert (browser.opened, browser.closed) == (1, 1)  # scrolling reused the one context


def test_scroll_at_max_steps_is_partial_and_keeps_every_record():
    growing = [page("A", "B"), page("A", "B", "C"), page("A", "B", "C", "D")]
    browser = FakeBrowser({"/one": page("A")}, growing)
    result = run(scroll_job(max_steps=2), browser).results[0]
    assert [record.name for record in result.items] == ["A", "B", "C"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [issue.code for issue in result.issues] == ["scroll_max_steps"]
    assert browser.scrolls == 2


def test_scroll_spends_the_request_budget_and_stops_at_it():
    growing = [page(*"ABCDEFGH"[: n + 2]) for n in range(8)]
    browser = FakeBrowser({"/one": page("A")}, growing)
    # robots + page = 2 requests; a budget of 4 leaves exactly two scroll steps.
    result = run(job(scroll={"max_steps": 30, "settle_ms": 100}, budget=4), browser).results[0]
    assert browser.scrolls == 2
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [issue.code for issue in result.issues] == ["scroll_budget"]
    assert [record.name for record in result.items] == ["A", "B", "C"]
    assert result.metrics.request_count == 4


def test_scroll_shares_the_budget_with_pagination():
    browser = FakeBrowser(
        {"/one": page("A", next="/two"), "/two": page("B")}, [page("A", "S1"), page("A", "S1")]
    )
    result = run(job(pagination=True, scroll={"max_steps": 5, "settle_ms": 100}), browser).results[
        0
    ]
    names = {record.name for record in result.items}
    assert {"A", "S1", "B"} <= names
    assert (browser.opened, browser.closed) == (1, 1)


def test_a_partial_scroll_page_does_not_stop_pagination():
    growing = [page("A", "B"), page("A", "B", "C")]
    browser = FakeBrowser({"/one": page("A", next="/two"), "/two": page("Z")}, growing)
    result = run(job(pagination=True, scroll={"max_steps": 1, "settle_ms": 100}), browser).results[
        0
    ]
    assert result.pagination_stop == "no_next"
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert "Z" in {record.name for record in result.items}


def test_scroll_on_http_is_ignored_and_says_so():
    from rastro.application.acquisition import WebAcquirer

    class HtmlTransport:
        async def get(self, target, **kwargs):
            if urlsplit(target.url).path == "/robots.txt":
                return HttpResponse(404, {}, b"", 0)
            body = page("A").encode()
            return HttpResponse(200, {"content-type": "text/html"}, body, len(body))

    spec = job(render="http", scroll={"max_steps": 3, "settle_ms": 100})
    http = WebAcquirer(spec.acquisition, transport=HtmlTransport(), resolver=Resolver())
    result = run_job(spec, acquisition=http, base_dir=Path.cwd()).results[0]
    assert result.status == Outcome.SUCCESS
    assert [record.name for record in result.items] == ["A"]
    assert [issue.code for issue in result.issues] == ["scroll_ignored_http"]


def test_a_failing_scroll_step_keeps_the_records_as_partial():
    class Failing(Session):
        async def scroll(self, *, policy, max_bytes, settle_ms):
            raise AcquisitionError("browser_navigation_failed", Outcome.TRANSIENT_FAILURE)

    class Browser(FakeBrowser):
        @asynccontextmanager
        async def session(self, targets, *, policy):
            yield Failing(self)

    result = run(scroll_job(), Browser({"/one": page("A", "B")})).results[0]
    assert [record.name for record in result.items] == ["A", "B"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [issue.code for issue in result.issues] == ["scroll_step_failed"]


def test_a_renderer_without_sessions_cannot_scroll_and_says_so():
    renderer = LegacyRenderer({"/one": page("A")})
    result = run(scroll_job(), renderer).results[0]
    assert [record.name for record in result.items] == ["A"]
    assert [issue.code for issue in result.issues] == ["scroll_step_failed"]


def test_a_shared_page_counter_caps_scroll_steps_too():
    from rastro.application.scroll import PAGE_COUNTER, PageCounter

    browser = FakeBrowser({"/one": page("A")}, [page(*"ABCDEF"[: n + 2]) for n in range(5)])
    token = PAGE_COUNTER.set(PageCounter(1))
    try:
        result = run(scroll_job(max_steps=10), browser).results[0]
    finally:
        PAGE_COUNTER.reset(token)
    assert browser.scrolls == 1
    assert [issue.code for issue in result.issues] == ["scroll_budget"]
    assert result.status == Outcome.PARTIAL_SUCCESS


def test_scroll_never_changes_what_the_render_mode_grants():
    """Scroll is a browser step: it requests no capability, so untrusted input may use it."""
    from rastro.application.capability_gate import requested_capabilities, require_capabilities
    from rastro.application.operator_trust import input_origin

    spec = scroll_job()
    assert requested_capabilities(spec) == frozenset()
    with input_origin("mcp"):
        require_capabilities(spec)
    assert spec.acquisition.render == "browser" and not spec.acquisition.solve_challenges


@pytest.mark.asyncio
async def test_job_routing_scrolls_only_browser_sources():
    from rastro.browser import _JobAcquisition

    class Client:
        def __init__(self):
            self.steps = 0

        def scrolls(self, url):
            return True

        async def scroll(self, source, *, settle_ms, max_bytes):
            self.steps += 1

    browser, http = Client(), object()
    routed = _JobAcquisition(
        {"https://agenda.test/b": "browser", "https://other.test/h": "http"},
        {"http": http, "browser": browser},  # type: ignore[dict-item]
    )
    assert routed.scrolls("https://agenda.test/b") and not routed.scrolls("https://other.test/h")
    await routed.scroll(Source(url="https://agenda.test/b"), settle_ms=100, max_bytes=10)
    assert browser.steps == 1


def test_job_routing_names_why_a_source_does_not_scroll():
    from rastro.browser import _JobAcquisition

    routed = _JobAcquisition(
        {"https://agenda.test/s": "stealth", "https://other.test/h": "http"}, {}
    )
    assert not routed.scrolls("https://agenda.test/s")  # M1: stealth does not scroll
    assert routed.scroll_skip_code("https://agenda.test/s") == "scroll_unsupported_stealth"
    assert routed.scroll_skip_code("https://other.test/h") == "scroll_ignored_http"


@pytest.mark.asyncio
async def test_job_routing_refuses_to_scroll_a_client_that_cannot():
    from rastro.browser import _JobAcquisition

    routed = _JobAcquisition({"https://agenda.test/h": "http"}, {"http": object()})  # type: ignore[dict-item]
    with pytest.raises(ConfigurationError):
        await routed.scroll(Source(url="https://agenda.test/h"), settle_ms=100, max_bytes=10)


def test_stealth_with_scroll_succeeds_and_says_scroll_is_unsupported(monkeypatch):
    """M1: a permanent limitation is a note on a successful run, not a recurring partial."""
    monkeypatch.setenv("RASTRO_ALLOW_STEALTH", "1")
    browser = FakeBrowser({"/one": page("A", "B")}, [page("A", "B", "C")])
    result = run(job(render="stealth", scroll={"max_steps": 3, "settle_ms": 100}), browser)
    result = result.results[0]
    assert result.status == Outcome.SUCCESS
    assert [record.name for record in result.items] == ["A", "B"]
    assert [issue.code for issue in result.issues] == ["scroll_unsupported_stealth"]
    assert browser.scrolls == 0
