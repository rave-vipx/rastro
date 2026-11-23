"""Studio highlight boxes: measured on a browser render under the acquirer's own policy."""

from contextlib import asynccontextmanager

import pytest

from rastro.adapters.element_boxes import browser_selector, measure_boxes
from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, HttpResponse
from rastro.domain.models import ConfigurationError, Outcome
from rastro.studio.routes_pick import BoxSource

PAGE = "<html><body><h2>Night Run</h2><h2>Dawn Walk</h2></body></html>"
BOX = {"x": 10.0, "y": 20.0, "w": 100.0, "h": 30.0}


class Page:
    def __init__(self, answer):
        self.answer, self.queries = answer, []

    async def evaluate(self, script, args):
        self.queries.append(args[0])
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_selectors_drop_extraction_pseudo_elements():
    assert browser_selector("h2.title::text") == "h2.title"
    assert browser_selector("a.more::attr(href)") == "a.more"
    assert browser_selector("li > b") == "li > b"


@pytest.mark.asyncio
async def test_boxes_are_keyed_by_recipe_selector_and_capped():
    page = Page([[BOX] * 80, []])
    boxes = await measure_boxes(page, ["h2::text", "p.none"], limit=50)
    assert page.queries == [["h2", "p.none"]]
    assert len(boxes["h2::text"]) == 50 and boxes["p.none"] == []
    assert await measure_boxes(Page(RuntimeError("closed")), ["h2"]) == {"h2": []}
    assert await measure_boxes(Page("not a list"), ["h2"]) == {"h2": []}


class Session:
    def __init__(self, owner):
        self.owner = owner

    async def render(self, target, *, policy, max_bytes):
        self.owner.rendered.append(target.url)
        return AcquisitionResult(target.url, PAGE, Outcome.SUCCESS, None, http_status=200)

    async def scroll(self, *, policy, max_bytes, settle_ms):
        raise AssertionError("not used")

    async def element_boxes(self, selectors):
        return {selector: [BOX] for selector in selectors}


class Browser:
    def __init__(self):
        self.rendered, self.closed = [], 0

    @asynccontextmanager
    async def session(self, targets, *, policy):
        try:
            yield Session(self)
        finally:
            self.closed += 1

    async def render(self, target, *, policy, max_bytes, targets):
        raise AssertionError("boxes are measured in a session")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    async def get(self, target, **kwargs):
        return HttpResponse(404, {}, b"", 0)  # robots.txt: none


def acquirer(browser, hosts=("agenda.example",)):
    policy = AcquisitionPolicy(allowed_hosts=hosts, render="browser", min_interval_seconds=0)
    return BrowserAcquirer(policy, renderer=browser, transport=Transport(), resolver=Resolver())


@pytest.mark.asyncio
async def test_browser_acquirer_measures_boxes_on_its_own_render():
    browser = Browser()
    client = acquirer(browser)
    assert isinstance(client, BoxSource)
    boxes = await client.element_boxes("https://agenda.example/", ["h2::text"])
    assert boxes == {"h2::text": [BOX]}
    assert browser.rendered == ["https://agenda.example/"] and browser.closed == 1


@pytest.mark.asyncio
async def test_a_refused_destination_measures_nothing():
    browser = Browser()
    assert await acquirer(browser).element_boxes("https://elsewhere.example/", ["h2"]) == {}
    assert browser.rendered == []


def test_studio_renders_the_page_in_a_browser_when_the_extra_is_installed(monkeypatch):
    from rastro.studio import routes_pick

    policy = AcquisitionPolicy(allowed_hosts=("agenda.example",), render="browser")
    monkeypatch.setattr(routes_pick, "browser_acquirer", lambda p: acquirer(Browser()))
    assert isinstance(routes_pick._page_acquirer(policy), BoxSource)

    def missing(p):
        raise ConfigurationError("This needs rastro-engine[browser]")

    monkeypatch.setattr(routes_pick, "browser_acquirer", missing)
    fallback = routes_pick._page_acquirer(policy)
    assert not isinstance(fallback, BoxSource) and fallback.policy.render == "http"
