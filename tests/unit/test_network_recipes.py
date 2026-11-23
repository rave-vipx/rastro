"""Network-first recipes: a JSON recipe over a response the page loaded, captured bounded."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, PinnedTarget
from rastro.domain.browser_controls import BrowserControls
from rastro.domain.capture_pattern import capture_matches
from rastro.domain.models import ConfigurationError, Limits, Outcome, Source
from rastro.domain.recipes import JsonRecipe, Recipe

TARGET = PinnedTarget("https://shop.example/list", "shop.example", 443, "93.184.216.34")
ITEMS = {"data": {"items": [{"name": "Lamp", "price": 12.5}, {"name": "Desk", "price": 80}]}}


def capture_policy(*patterns: str, **changes: object) -> AcquisitionPolicy:
    return AcquisitionPolicy(
        allowed_hosts=("shop.example",),
        render="browser",
        min_interval_seconds=0,
        browser={"capture_network": patterns or (r"^/api/",)},
        **changes,
    )


class FakeResponse:
    def __init__(self, url, body, *, status=200, media="application/json", kind="xhr", length=None):
        headers = {"content-type": media}
        if length is not None:
            headers["content-length"] = str(length)
        self.url, self.status, self.headers, self._body = url, status, headers, body
        self.request = SimpleNamespace(method="GET", resource_type=kind)

    async def body(self):
        return self._body


class FakePage:
    """A page whose navigation emits the given responses to its listeners."""

    def __init__(self, responses):
        self.responses, self.url, self.listeners = responses, "", []

    def on(self, event, handler):
        assert event == "response"
        self.listeners.append(handler)

    async def goto(self, url, **kwargs):
        self.url = url
        for response in self.responses:
            for handler in self.listeners:
                handler(response)
        return SimpleNamespace(status=200)

    async def content(self):
        return "<html><body><div id=app></div></body></html>"

    async def evaluate(self, script):
        return "<html><body><template shadowrootmode=open><p>inside</p></template></body></html>"


def fake_session(monkeypatch, responses):
    from rastro.adapters.playwright_browser import NetworkGuard
    from rastro.adapters.playwright_session import PlaywrightSession

    async def setup(self, page):
        return None

    monkeypatch.setattr(NetworkGuard, "setup", setup)
    page = FakePage(responses)

    async def new_page():
        return page

    policy = capture_policy()
    return PlaywrightSession(SimpleNamespace(new_page=new_page), NetworkGuard(policy)), policy


@pytest.mark.asyncio
async def test_capture_xhr_json_builds_recipe(monkeypatch):
    from rastro.adapters.structured import JsonDiscovery, JsonParser
    from rastro.application.network_recipes import (
        capture_offers,
        captured_document,
        recipe_from_capture,
    )
    from rastro.ports.engine import NetworkCapture

    body = json.dumps(ITEMS).encode()
    session, policy = fake_session(
        monkeypatch,
        [
            FakeResponse("https://shop.example/api/items?page=1", body),
            FakeResponse("https://shop.example/app.js", b"x", media="text/javascript"),
        ],
    )
    page = await session.render(TARGET, policy=policy, max_bytes=100_000)
    assert isinstance(session, NetworkCapture)
    captured = session.captured()
    assert [(item.host, item.path) for item in captured] == [("shop.example", "/api/items")]

    offer = capture_offers(captured)[
        0
    ]  # what Studio offers: "this page loads its data from an API"
    assert (offer.path, offer.record_path, offer.status) == ("/api/items", "/data/items", 200)

    recipes = recipe_from_capture(
        captured[0], ("name", "price"), discovery=JsonDiscovery(), limits=Limits()
    )
    recipe = recipes[0]
    assert isinstance(recipe, JsonRecipe) and recipe.capture == r"^/api/items$"
    assert Recipe.model_validate(recipe.model_dump()) == recipe  # a recipe is plain data

    document = captured_document(page, captured, recipe.capture)
    assert document.url == TARGET.url  # the job URL stays the page
    parsed = JsonParser().parse(document.body or "", recipe, Limits())
    assert [c.values for c in parsed.candidates] == ITEMS["data"]["items"]


@pytest.mark.asyncio
async def test_capture_query_never_stored(monkeypatch):
    from rastro.application.network_recipes import capture_offers

    secret = "token=s3cret-value"
    session, policy = fake_session(
        monkeypatch,
        [FakeResponse(f"https://shop.example/api/me?{secret}#frag", b'{"ok": true}')],
    )
    await session.render(TARGET, policy=policy, max_bytes=100_000)
    (item,) = session.captured()
    assert item.path == "/api/me" and "s3cret" not in repr(item)
    assert "s3cret" not in repr(capture_offers((item,)))
    assert not hasattr(item, "url") and not hasattr(item, "headers")


def test_capture_bounded_bytes_and_count():
    from rastro.application.network_recipes import (
        MAX_CAPTURED_RESPONSES,
        MAX_RESPONSE_BYTES,
        CaptureBuffer,
    )

    buffer = CaptureBuffer(capture_policy())
    url = "https://shop.example/api/items"
    refused = [
        buffer.wants("https://other.example/api/items", "GET", 200, "application/json", None),
        buffer.wants(url, "GET", 200, "text/html", None),
        buffer.wants(url, "DELETE", 200, "application/json", None),
        buffer.wants("https://shop.example/static/a", "GET", 200, "application/json", None),
        buffer.wants(url, "GET", 200, "application/json", str(MAX_RESPONSE_BYTES + 1)),
        buffer.wants("http://shop.example/api/items", "GET", 200, "application/json", None),
    ]
    assert refused == [None] * len(refused)

    ticket = buffer.wants(url, "GET", 200, "application/problem+json; charset=utf-8", "10")
    assert ticket is not None
    assert not buffer.add(ticket, b"x" * (MAX_RESPONSE_BYTES + 1))  # the header lied: dropped
    tickets = [buffer.wants(url, "POST", 200, "application/json", None) for _ in range(40)]
    accepted = [item for item in tickets if item is not None]
    assert len(accepted) == MAX_CAPTURED_RESPONSES - 1  # the dropped one still spent its place
    for item in accepted:
        buffer.add(item, b"[]")
    assert len(buffer.responses) == MAX_CAPTURED_RESPONSES - 1
    buffer.close()
    assert buffer.wants(url, "GET", 200, "application/json", None) is None


def test_capture_total_bytes_bounded():
    from rastro.application.network_recipes import (
        MAX_CAPTURED_BYTES,
        MAX_RESPONSE_BYTES,
        CaptureBuffer,
    )

    buffer = CaptureBuffer(capture_policy())
    kept = 0
    for _ in range(MAX_CAPTURED_BYTES // MAX_RESPONSE_BYTES + 2):
        ticket = buffer.wants("https://shop.example/api/x", "GET", 200, "text/json", None)
        if ticket is not None and buffer.add(ticket, b"1" * MAX_RESPONSE_BYTES):
            kept += 1
    assert kept * MAX_RESPONSE_BYTES <= MAX_CAPTURED_BYTES
    assert sum(len(item.body) for item in buffer.responses) <= MAX_CAPTURED_BYTES


def test_capture_off_without_patterns():
    from rastro.application.network_recipes import CaptureBuffer

    plain = AcquisitionPolicy(allowed_hosts=("shop.example",), render="browser")
    assert (
        CaptureBuffer(plain).wants(
            "https://shop.example/api/items", "GET", 200, "application/json", None
        )
        is None
    )


def test_json_shape_drift_detected():
    from rastro.application.drift import snapshot_from_document
    from rastro.domain.drift import assess_drift

    recipe = JsonRecipe(record_path="/data/items", fields={"name": "/name", "price": "/price"})

    def snapshot(payload, run):
        from rastro.domain.models import ExtractionResult

        result = ExtractionResult.model_construct(
            job_id=run,
            source_url="https://shop.example/list",
            schema_fingerprint="a" * 64,
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            locale="und",
            timezone="UTC",
            metrics=SimpleNamespace(egress="direct", candidates=2),
            status=Outcome.SUCCESS,
            records=(),
            issues=(),
            recipe_attempts=(),
        )
        return snapshot_from_document(result, json.dumps(payload), recipe, Limits())

    before = snapshot(ITEMS, "r1")
    same = snapshot(ITEMS, "r2")
    changed = {"data": {"items": [{"name": "Lamp", "price": {"amount": 12.5}}]}}
    after = snapshot(changed, "r3")
    assert before.fields["price"].subtree_fingerprint is not None
    assert assess_drift(before, same).verdict == "none"
    assessment = assess_drift(before, after)
    assert assessment.verdict == "structural_drift"
    assert {signal.field for signal in assessment.signals} == {"price"}


def test_recipe_capture_is_validated_data():
    recipe = JsonRecipe(record_path="", fields={"a": "/a"}, capture=r"^/api/v\d+/items$")
    assert recipe.capture == r"^/api/v\d+/items$"
    assert "capture" not in JsonRecipe(record_path="", fields={"a": "/a"}).model_dump()
    for bad in ["(", "", "x" * 257, r"(?=/api)", r"(a)\1"]:
        with pytest.raises(ValidationError):
            JsonRecipe(record_path="", fields={"a": "/a"}, capture=bad)


def test_capture_pattern_runs_in_linear_time_like_spider_patterns():
    # A nested quantifier is harmless on the linear-time engine shared with allow_pattern.
    recipe = JsonRecipe(record_path="", fields={"a": "/a"}, capture=r"^/(a+)+$")
    started = time.perf_counter()
    assert not capture_matches(recipe.capture or "", "/" + "a" * 1000 + "!")
    assert time.perf_counter() - started < 0.5
    assert capture_matches(r"api/v\d", "/x/api/v2/items")
    with pytest.raises(ValidationError):
        BrowserControls(capture_network=(r"(?<=x)y",))
    with pytest.raises(ValidationError):
        JsonRecipe(
            record_path="",
            fields={"a": "/a"},
            fallbacks=(
                {"strategy": "json", "record_path": "", "fields": {"a": "/b"}, "capture": "^/x"},
            ),
        )


def page_result(body="<p>page</p>"):
    return AcquisitionResult(TARGET.url, body, Outcome.SUCCESS, None, http_status=200)


def test_captured_document_selects_by_recipe_pattern_or_fails_visibly():
    from rastro.application.network_recipes import captured_document
    from rastro.ports.engine import CapturedResponse

    first = CapturedResponse("shop.example", "/api/menu", "GET", 200, "application/json", b"[]")
    second = CapturedResponse("shop.example", "/api/items", "GET", 200, "application/json", b"{}")
    failed = CapturedResponse("shop.example", "/api/items", "GET", 500, "application/json", b"1")
    assert captured_document(page_result(), (first, second), None).body == "[]"
    assert captured_document(page_result(), (failed, first, second), "items$").body == "{}"
    empty = captured_document(page_result(), (first,), "items$")
    assert (empty.body, empty.status, empty.issue_code) == (
        None,
        Outcome.INVALID_DATA,
        "capture_empty",
    )
    gone = AcquisitionResult(TARGET.url, None, Outcome.TRANSIENT_FAILURE, "browser_failure")
    assert captured_document(gone, (second,), None) is gone


class CapturingSession:
    def __init__(self, responses):
        self.responses, self.rendered = responses, 0

    async def render(self, target, *, policy, max_bytes):
        self.rendered += 1
        return page_result()

    async def scroll(self, **kwargs):  # pragma: no cover - not used here
        raise AssertionError

    def captured(self):
        return self.responses


class CapturingRenderer:
    def __init__(self, responses):
        self.session_used = CapturingSession(responses)

    async def render(self, target, **kwargs):  # pragma: no cover - sessions are used
        raise AssertionError

    @asynccontextmanager
    async def session(self, targets, *, policy):
        yield self.session_used


def acquirer(policy, responses):
    from unittest.mock import AsyncMock

    from rastro.application.browser_acquisition import BrowserAcquirer
    from rastro.domain.acquisition import HttpResponse

    transport = AsyncMock()
    transport.get.return_value = HttpResponse(404, {}, b"", 0)
    resolver = AsyncMock()
    resolver.resolve.return_value = ("93.184.216.34",)
    renderer = CapturingRenderer(responses)
    client = BrowserAcquirer(policy, renderer=renderer, transport=transport, resolver=resolver)
    return client, renderer


@pytest.mark.asyncio
async def test_browser_json_source_reads_the_selected_capture():
    from rastro.application.network_recipes import selecting_capture
    from rastro.ports.engine import CapturedResponse

    menu = CapturedResponse("shop.example", "/api/menu", "GET", 200, "application/json", b"[1]")
    items = CapturedResponse("shop.example", "/api/items", "GET", 200, "application/json", b"[2]")
    client, _ = acquirer(capture_policy(), (menu, items))
    source = Source(url=TARGET.url)
    result = await client.acquire(source, max_bytes=10_000, representation="json")
    assert (result.url, result.body, result.status) == (TARGET.url, "[1]", Outcome.SUCCESS)
    recipe = JsonRecipe(record_path="", fields={"a": "/a"}, capture="/items$")
    with selecting_capture(recipe):
        chosen = await client.acquire(source, max_bytes=10_000, representation="json")
    assert chosen.body == "[2]"


@pytest.mark.asyncio
async def test_browser_json_source_needs_capture_patterns():
    plain = AcquisitionPolicy(allowed_hosts=("shop.example",), render="browser")
    client, _ = acquirer(plain, ())
    with pytest.raises(ConfigurationError):
        await client.acquire(Source(url=TARGET.url), max_bytes=10_000, representation="json")


@pytest.mark.asyncio
async def test_capturable_json_lists_offers_for_a_page():
    from rastro.application.network_recipes import capturable_json, offer_policy
    from rastro.ports.engine import CapturedResponse

    items = CapturedResponse(
        "shop.example", "/api/items", "GET", 200, "application/json", json.dumps(ITEMS).encode()
    )
    plain = AcquisitionPolicy(allowed_hosts=("shop.example",), render="browser")
    assert offer_policy(plain).browser.capture_network
    client, _ = acquirer(offer_policy(plain), (items,))
    offers = await capturable_json(client, Source(url=TARGET.url), max_bytes=10_000)
    assert [(o.path, o.record_path, o.records) for o in offers] == [
        ("/api/items", "/data/items", 2)
    ]


@pytest.mark.asyncio
async def test_playwright_serializes_open_shadow_roots_when_asked(monkeypatch):
    from rastro.adapters.playwright_launch import load_page

    page = FakePage([])
    guard = SimpleNamespace(
        result=lambda url, body, status, max_bytes: page_result(body),
    )
    pierce = AcquisitionPolicy(
        allowed_hosts=("shop.example",), render="browser", browser={"pierce_shadow": True}
    )
    shadow = await load_page(page, guard, TARGET.url, pierce, 10_000)
    assert "inside" in (shadow.body or "")
    plain = await load_page(page, guard, TARGET.url, capture_policy(), 10_000)
    assert "inside" not in (plain.body or "")


def test_documented_job_runs_a_json_source_in_the_browser():
    import re
    from pathlib import Path

    from rastro.browser import job_render_modes
    from rastro.domain.jobs import parse_job

    page = (Path(__file__).parents[2] / "docs" / "network-recipes.md").read_text()
    snippet = re.search(r"```toml\n(.*?)```", page, re.S)
    assert snippet is not None
    job = parse_job(snippet.group(1), format="toml")
    assert job_render_modes(job) == {"https://shop.example/list": "browser"}
    assert job.sources[0].recipe.capture == "^/api/items$"
    without = snippet.group(1).replace("capture_network = ['^/api/']", "")
    with pytest.raises(ConfigurationError):
        job_render_modes(parse_job(without, format="toml"))
