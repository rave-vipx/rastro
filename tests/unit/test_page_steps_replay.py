"""A recipe's page steps replay in the source's browser page before extraction.

The fake session runs the real ``perform_steps`` against a synthetic page, as a browser
session does, so the whole path from job to records is exercised without a browser.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

from rastro.adapters.browser_steps import perform_steps
from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.jobs import run_job
from rastro.application.page_steps import steps_budget_ms
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class TimeoutError_(Exception):
    pass


TimeoutError_.__name__ = "TimeoutError"


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    def __init__(self, robots=None):
        self.robots = robots

    async def get(self, target, **kwargs):
        if self.robots is not None:
            body = self.robots.encode()
            return HttpResponse(200, {"content-type": "text/plain"}, body, len(body))
        return HttpResponse(404, {}, b"", 0)


def doc(*names, more=False, next_href=None):
    items = "".join(f"<article><h2>{n}</h2></article>" for n in names)
    button = '<button class="more">More</button>' if more else ""
    link = f'<a class="next" href="{next_href}">next</a>' if next_href else ""
    return f"<html><body>{items}{button}{link}</body></html>"


class Page:
    """A synthetic page: 'button.more' reveals the second half of the list."""

    def __init__(self, site):
        self.site, self.url, self.expanded = site, "", False

    def body(self):
        path = urlsplit(self.url).path
        if path == "/list":
            names = ("A", "B", "C", "D") if self.expanded else ("A", "B")
            return doc(*names, more=not self.expanded, next_href=self.site.next_href)
        return doc("Detail")

    async def wait_for_selector(self, selector, *, state, timeout):
        css = selector.removeprefix("css=")
        if state in ("attached", "visible") and css not in self.present():
            raise TimeoutError_("timeout")

    def present(self):
        found = {"article", "h2"}
        if not self.expanded and urlsplit(self.url).path == "/list":
            found.add("button.more")
        if self.site.next_href:
            found.add("a.next")
        return found

    async def click(self, selector, *, timeout):
        if selector == "css=button.more":
            self.expanded = True

    async def get_attribute(self, selector, name, *, timeout):
        return self.site.next_href

    async def goto(self, url, *, timeout, wait_until):
        self.site.visited.append(url)
        self.url = urljoin(self.url, url)

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, expression):
        self.site.scrolls += 1


class Guard:
    def __init__(self):
        self.policy, self.requests, self.issue = AcquisitionPolicy(allowed_hosts=["x"]), 0, None


class Session:
    def __init__(self, site):
        self.page, self.site, self.guard = Page(site), site, Guard()

    async def render(self, target, *, policy, max_bytes):
        self.page.url, self.page.expanded = target.url, False
        return AcquisitionResult(
            target.url, self.page.body(), Outcome.SUCCESS, None, http_status=200, requests=1
        )

    async def scroll(self, *, policy, max_bytes, settle_ms):  # pragma: no cover - unused
        raise AssertionError

    async def run_steps(self, steps, *, policy, max_bytes):
        self.site.step_policies.append(policy)
        self.guard.policy, self.guard.requests = policy, 1
        await perform_steps(
            self.page, steps, guard=self.guard, timeout_ms=steps_budget_ms(steps, policy)
        )
        return AcquisitionResult(
            self.page.url, self.page.body(), Outcome.SUCCESS, None, http_status=200, requests=1
        )


class Site:
    def __init__(self, next_href=None):
        self.next_href, self.visited, self.step_policies = next_href, [], []
        self.scrolls = 0

    @asynccontextmanager
    async def session(self, targets, *, policy):
        yield Session(self)


def job(steps, *, render="browser", hosts=("shop.example",)):
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://shop.example/list"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {
                "strategy": "css",
                "record_selector": "article",
                "fields": {"name": "h2::text"},
                "steps": steps,
            },
            "acquisition": {
                "allowed_hosts": list(hosts),
                "min_interval_seconds": 0,
                "max_retries": 0,
                "render": render,
            },
        }
    )


def run(spec, site, transport=None):
    acquirer = BrowserAcquirer(
        spec.acquisition, renderer=site, transport=transport or Transport(), resolver=Resolver()
    )
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd()).results[0]


def test_click_load_more_then_extract():
    result = run(job([{"action": "click", "selector": "button.more"}]), Site())
    assert result.status == Outcome.SUCCESS
    assert [r.name for r in result.items] == ["A", "B", "C", "D"]


def test_step_selector_missing_reports_drift():
    result = run(job([{"action": "click", "selector": "button.load-all"}]), Site())
    assert result.status == Outcome.INVALID_DATA  # a structural change, not an empty success
    assert result.items == []
    issue = next(i for i in result.issues if i.code == "step_selector_missing")
    assert issue.message == "step 1 (click)"


def test_follow_step_target_checked_by_allowlist():
    site = Site(next_href="https://elsewhere.example/list")
    result = run(job([{"action": "follow", "selector": "a.next::attr(href)"}]), site)
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert site.visited == []
    assert result.items == []


def test_a_followed_page_still_obeys_robots():
    site = Site(next_href="/private/detail")
    robots = Transport(robots="User-agent: *\nDisallow: /private/\n")
    result = run(job([{"action": "follow", "selector": "a.next::attr(href)"}]), site, robots)
    assert result.items == []
    assert any(i.code == "robots_denied" for i in result.issues)


def test_steps_on_an_http_source_are_not_silently_skipped():
    from rastro.application.acquisition import WebAcquirer

    class Html(Transport):
        async def get(self, target, **kwargs):
            if urlsplit(target.url).path == "/robots.txt":
                return HttpResponse(404, {}, b"", 0)
            body = doc("A", "B", more=True).encode()
            return HttpResponse(200, {"content-type": "text/html"}, body, len(body))

    spec = job([{"action": "click", "selector": "button.more"}], render="http")
    acquirer = WebAcquirer(spec.acquisition, transport=Html(), resolver=Resolver())
    result = run_job(spec, acquisition=acquirer, base_dir=Path.cwd()).results[0]
    assert result.status != Outcome.SUCCESS
    assert any(i.code == "step_failed" for i in result.issues)


def test_steps_spend_the_shared_request_budget():
    site = Site()
    run(job([{"action": "click", "selector": "button.more"}]), site)
    (policy,) = site.step_policies
    assert policy.max_requests < 30  # the steps get only what the navigation left


def test_a_browser_session_without_steps_support_fails_the_source():
    class Plain(Session):
        run_steps = None  # type: ignore[assignment]

    class OldSite(Site):
        @asynccontextmanager
        async def session(self, targets, *, policy):
            yield Plain(self)

    result = run(job([{"action": "click", "selector": "button.more"}]), OldSite())
    assert result.items == []
    assert any(i.code == "step_failed" for i in result.issues)


def test_steps_that_end_on_a_host_outside_the_job_are_refused():
    class Escaping(Session):
        async def run_steps(self, steps, *, policy, max_bytes):
            return AcquisitionResult(
                "https://elsewhere.example/x", doc("Z"), Outcome.SUCCESS, None, requests=1
            )

    class EscapingSite(Site):
        @asynccontextmanager
        async def session(self, targets, *, policy):
            yield Escaping(self)

    result = run(job([{"action": "click", "selector": "button.more"}]), EscapingSite())
    assert result.items == []
    assert result.status == Outcome.ACCESS_RESTRICTION


def test_steps_that_never_finish_are_cut_at_their_deadline(monkeypatch):
    import asyncio

    import rastro.application.browser_page_steps as module

    class Hanging(Session):
        async def run_steps(self, steps, *, policy, max_bytes):
            await asyncio.sleep(60)

    class HangingSite(Site):
        @asynccontextmanager
        async def session(self, targets, *, policy):
            yield Hanging(self)

    monkeypatch.setattr(module, "steps_budget_ms", lambda steps, policy: 0)
    spec = job([{"action": "click", "selector": "button.more"}])
    spec = spec.model_copy(
        update={"acquisition": spec.acquisition.model_copy(update={"request_timeout_seconds": 0.1})}
    )
    result = run(spec, HangingSite())
    assert result.status == Outcome.TRANSIENT_FAILURE
    assert any(i.code == "step_failed" for i in result.issues)


def test_scroll_steps_spend_the_crawl_page_cap():
    from rastro.application.scroll import PAGE_COUNTER, PageCounter

    spec = job([{"action": "scroll", "times": 3, "settle_ms": 0}])
    free = Site()
    assert run(spec, free).status == Outcome.SUCCESS and free.scrolls == 3
    counter, capped = PageCounter(remaining=1), Site()
    token = PAGE_COUNTER.set(counter)
    try:
        result = run(spec, capped)
    finally:
        PAGE_COUNTER.reset(token)
    assert any(i.code == "step_failed" for i in result.issues)
    assert capped.scrolls == 0
    assert counter.remaining == 1  # nothing spent on steps that never ran


@pytest.mark.asyncio
async def test_steps_of_a_stored_recipe_are_refused_for_an_untrusted_origin(monkeypatch):
    from rastro.application.operator_trust import input_origin
    from rastro.application.page_steps import stepped

    for name in ("RASTRO_ALLOW_CAPABILITIES", "RASTRO_ALLOW_UNTRUSTED"):
        monkeypatch.delenv(name, raising=False)
    seen = []

    class Acquisition:
        def runs_steps(self, url):
            return True

        async def run_steps(self, source, steps, *, max_bytes):  # pragma: no cover - refused
            raise AssertionError("steps ran")

    async def extract(fetched):
        seen.append((fetched.status, fetched.issue_code))
        return fetched

    spec = job([{"action": "click", "selector": "button.more"}])
    wrapped = stepped(
        extract, steps=spec.recipe.steps, acquisition=Acquisition(), limits=spec.limits
    )
    page = AcquisitionResult("https://shop.example/list", doc("A"), Outcome.SUCCESS, None)
    with input_origin("mcp"):
        await wrapped(page)
    assert seen == [(Outcome.ACCESS_RESTRICTION, "capability_refused")]
