"""SCR-003 amended, SEC-005/008, OBS-001: browser boundary acceptance."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from rastro.domain.acquisition import (
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
)
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.models import ConfigurationError, Outcome, Source


def test_render_contract():
    assert AcquisitionPolicy(allowed_hosts=("example.com",)).render == "http"
    assert UrlJobSource(url="https://example.com").render == "http"
    for model, args in [
        (AcquisitionPolicy, {"allowed_hosts": ("example.com",)}),
        (UrlJobSource, {"url": "https://example.com"}),
    ]:
        with pytest.raises(ValidationError):
            model(**args, render="magic")
    assert AcquisitionResult("https://example.com", None, Outcome.SUCCESS, None).render_seconds == 0


def setup(mode="browser", **changes):
    from rastro.application.browser_acquisition import BrowserAcquirer

    changes.setdefault("allowed_hosts", ("example.com",))
    policy = AcquisitionPolicy(render=mode, min_interval_seconds=0, **changes)
    renderer = AsyncMock()
    renderer.render.return_value = AcquisitionResult(
        "https://example.com/", "<p>Rendered</p>", Outcome.SUCCESS, None, http_status=200
    )
    transport = AsyncMock()
    transport.get.return_value = HttpResponse(404, {}, b"", 0)
    resolver = AsyncMock()
    resolver.resolve.return_value = ("93.184.216.34",)
    client = BrowserAcquirer(
        policy, renderer=renderer, transport=transport, resolver=resolver, allow_stealth=True
    )
    return client, renderer, transport, resolver


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["browser", "stealth"])
async def test_rendered_document(mode):
    client, renderer, _, _ = setup(mode)
    result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.body == "<p>Rendered</p>"
    assert result.http_status == 200
    assert result.render_seconds > 0
    renderer.render.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,issue",
    [
        ("private", "destination_denied"),
        ("robots", "robots_denied"),
        ("oversize", "response_too_large"),
        ("offhost", "host_not_allowed"),
    ],
)
async def test_failures(case, issue):
    from dataclasses import replace

    client, renderer, transport, resolver = setup()
    if case == "private":
        resolver.resolve.return_value = ("127.0.0.1",)
    if case == "robots":
        transport.get.return_value = HttpResponse(
            200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /", 25
        )
    if case == "offhost":
        renderer.render.return_value = replace(
            renderer.render.return_value, url="https://evil.test/"
        )
    result = await client.acquire(
        Source(url="https://example.com/"), max_bytes=1 if case == "oversize" else 100
    )
    assert result.issue_code == issue
    assert result.body is None
    if case in {"private", "robots"}:
        renderer.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_robots_are_checked_for_the_redirected_document():
    from dataclasses import replace

    client, renderer, transport, _ = setup()
    transport.get.return_value = HttpResponse(
        200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private", 32
    )
    renderer.render.return_value = replace(
        renderer.render.return_value, url="https://example.com/private/page"
    )
    result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.issue_code == "robots_denied"
    assert result.body is None
    renderer.render.assert_awaited_once()


@pytest.mark.asyncio
async def test_redirect_to_an_allowed_path_succeeds():
    from dataclasses import replace

    client, renderer, transport, _ = setup()
    transport.get.return_value = HttpResponse(
        200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private", 32
    )
    renderer.render.return_value = replace(
        renderer.render.return_value, url="https://example.com/public"
    )
    result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.status == Outcome.SUCCESS


@pytest.mark.parametrize("changes", [{"render": "stealth"}, {"solve_challenges": True}])
def test_consent(changes, monkeypatch):
    from rastro.application.browser_acquisition import BrowserAcquirer

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    with pytest.raises(ConfigurationError, match="--allow-stealth"):
        BrowserAcquirer(
            AcquisitionPolicy(allowed_hosts=("example.com",), **changes),
            renderer=AsyncMock(),
            transport=AsyncMock(),
            resolver=AsyncMock(),
        )


def test_routes_and_dns():
    from rastro.adapters.playwright_browser import resolver_rules, route_allowed

    policy = AcquisitionPolicy(allowed_hosts=("example.com",))
    assert route_allowed("https://example.com/a.js", "script", policy)
    for url, kind in [
        ("https://other.test/x", "script"),
        ("file:///etc/passwd", "document"),
        ("http://example.com", "document"),
        ("https://example.com/a", "image"),
    ]:
        assert not route_allowed(url, kind, policy)
    assert (
        resolver_rules((PinnedTarget("https://example.com", "example.com", 443, "93.184.216.34"),))
        == "MAP example.com 93.184.216.34, MAP * ~NOTFOUND, EXCLUDE localhost"
    )


def test_missing_extra(monkeypatch):
    import importlib

    from rastro.adapters.playwright_browser import PlaywrightBrowser

    monkeypatch.setattr(
        importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError())
    )
    with pytest.raises(
        ConfigurationError,
        match=r"rastro-engine\[browser\].*playwright install chromium.*scrapling install",
    ):
        PlaywrightBrowser()


def test_egress_rejected():
    with pytest.raises(ConfigurationError, match="egress"):
        setup(egress_profiles={"proxy": "https://proxy.test"})


def test_job_routing_and_conflicts():
    from rastro.browser import job_render_modes
    from rastro.domain.jobs import Job

    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "discover_recipes": True,
            "sources": [
                {"url": "https://example.com/a"},
                {"url": "https://example.com/b", "render": "http"},
            ],
            "acquisition": {"allowed_hosts": ["example.com"], "render": "browser"},
        }
    )
    assert job_render_modes(job) == {
        "https://example.com/a": "browser",
        "https://example.com/b": "http",
    }
    conflict = job.model_copy(
        update={
            "sources": (
                UrlJobSource(url="https://example.com/a", render="http"),
                UrlJobSource(url="https://example.com/a", render="browser"),
            )
        }
    )
    with pytest.raises(ConfigurationError, match="conflicting"):
        job_render_modes(conflict)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["browser", "stealth"])
async def test_rendered_dom_extracts_records(mode):
    from pydantic import BaseModel

    import rastro

    class Item(BaseModel):
        title: str

    client, _, _, _ = setup(mode)
    result = await rastro.Rastro(acquisition=client, policy=client.policy).extract(
        source=Source(url="https://example.com/"),
        schema=Item,
        recipe=rastro.CssRecipe(record_selector="p", fields={"title": "::text"}),
    )
    assert result.status == Outcome.SUCCESS
    assert result.records[0].data.title == "Rendered"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 451])
async def test_http_restrictions_are_not_retried(status):
    from dataclasses import replace

    client, renderer, _, _ = setup()
    renderer.render.return_value = replace(renderer.render.return_value, http_status=status)
    result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.body is None
    renderer.render.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_budget_prevents_launch():
    client, renderer, _, _ = setup(max_requests=1)
    result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "request_limit"
    renderer.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_request_outside_pagination_scope_is_never_rendered():
    from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope

    client, renderer, _, _ = setup(allowed_hosts=("example.com", "other.test"))
    token = PAGINATION_SCOPE.set(PaginationScope("example.com", True, 5))
    try:
        result = await client.acquire(Source(url="https://other.test/page"), max_bytes=100)
    finally:
        PAGINATION_SCOPE.reset(token)
    assert result.issue_code == "off_host"
    assert result.body is None
    renderer.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_render_spends_the_shared_pagination_budget():
    from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope

    client, renderer, _, _ = setup()
    token = PAGINATION_SCOPE.set(PaginationScope("example.com", True, 1))  # robots spends it
    try:
        result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    finally:
        PAGINATION_SCOPE.reset(token)
    assert result.issue_code == "request_limit"
    renderer.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_redirect_outside_pagination_scope_is_refused():
    from dataclasses import replace

    from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope

    client, renderer, _, _ = setup(allowed_hosts=("example.com", "other.test"))
    renderer.render.return_value = replace(
        renderer.render.return_value, url="https://other.test/page"
    )
    token = PAGINATION_SCOPE.set(PaginationScope("example.com", True, 5))
    try:
        result = await client.acquire(Source(url="https://example.com/"), max_bytes=100)
    finally:
        PAGINATION_SCOPE.reset(token)
    assert result.issue_code == "off_host"
    assert result.body is None


@pytest.mark.asyncio
async def test_network_guard_checks_redirects_and_budget():
    from types import SimpleNamespace

    from rastro.adapters.playwright_browser import NetworkGuard
    from rastro.domain.acquisition import AcquisitionError

    policy = AcquisitionPolicy(allowed_hosts=("example.com",), max_requests=1)
    guard = NetworkGuard(policy)
    page = AsyncMock()
    session = AsyncMock()
    handlers = {}
    session.on = lambda event, callback: handlers.update({event: callback})
    page.context.new_cdp_session.return_value = session
    await guard.setup(page)
    paused = handlers["Fetch.requestPaused"]
    await paused(
        {"requestId": "1", "request": {"url": "https://example.com/"}, "resourceType": "Document"}
    )
    assert session.send.call_args.args[0] == "Fetch.continueRequest"
    await paused(
        {"requestId": "2", "request": {"url": "https://evil.test/"}, "resourceType": "Document"}
    )
    assert session.send.call_args.args[0] == "Fetch.failRequest"
    assert guard.issue == "request_limit"
    with pytest.raises(AcquisitionError) as error:
        guard.result("https://example.com/", "<p>x</p>", 200, 100)
    assert (error.value.code, error.value.outcome) == ("request_limit", Outcome.PERMANENT_FAILURE)
    route = AsyncMock()
    route.request = SimpleNamespace(
        url="https://evil.test/x", resource_type="script", frame=SimpleNamespace(page=page)
    )
    await page.context.route.call_args.args[1](route)
    route.abort.assert_awaited_once()


@pytest.mark.asyncio
async def test_guard_setup_failure_closes_page():
    from rastro.adapters.playwright_browser import NetworkGuard
    from rastro.domain.acquisition import AcquisitionError

    page = AsyncMock()
    page.context.route.side_effect = RuntimeError("synthetic sensitive detail")
    with pytest.raises(AcquisitionError, match="browser_guard_failed"):
        await NetworkGuard(AcquisitionPolicy(allowed_hosts=("example.com",))).setup(page)
    page.close.assert_awaited_once()


def test_unsolved_challenge_restricted():
    from rastro.adapters.playwright_browser import NetworkGuard
    from rastro.domain.acquisition import AcquisitionError

    guard = NetworkGuard(AcquisitionPolicy(allowed_hosts=("example.com",)))
    with pytest.raises(AcquisitionError, match="challenge_unsolved"):
        guard.result("https://example.com/", '<div class="cf-chl-form">Wait</div>', 200, 100)


def test_render_inheritance_survives_serialization():
    from rastro.domain.job_sources import UrlJobSource

    source = UrlJobSource(url="https://example.com/")
    assert (
        "render" not in UrlJobSource.model_validate_json(source.model_dump_json()).model_fields_set
    )


@pytest.mark.asyncio
async def test_sdk_stealth_consent(monkeypatch):
    from rastro.application.jobs import run_job_async
    from rastro.domain.jobs import Job

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "sources": [{"url": "https://example.com/", "render": "stealth"}],
            "recipe": {"strategy": "css", "record_selector": "p", "fields": {"title": "::text"}},
            "acquisition": {"allowed_hosts": ["example.com"]},
        }
    )
    acquisition = AsyncMock()
    acquisition.acquire.return_value = AcquisitionResult(
        "https://example.com/", "<p>Rendered</p>", Outcome.SUCCESS, None
    )
    with pytest.raises(ConfigurationError, match="--allow-stealth"):
        await run_job_async(job, acquisition=acquisition)
    acquisition.acquire.assert_not_awaited()
    result = await run_job_async(job, acquisition=acquisition, allow_stealth=True)
    assert result.job.status == "success"


def test_missing_browser_extra_checked_before_http_import(monkeypatch):
    import builtins

    from rastro.adapters.playwright_browser import INSTALL, PlaywrightBrowser
    from rastro.browser import job_acquisition
    from rastro.domain.jobs import Job

    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "sources": [{"url": "https://example.com/", "render": "browser"}],
            "discover_recipes": True,
            "acquisition": {"allowed_hosts": ["example.com"]},
        }
    )
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "rastro.adapters.http", "HTTP imported before missing-extra diagnostic"
        return original(name, *args, **kwargs)

    def missing(self):
        raise ConfigurationError(INSTALL)

    monkeypatch.setattr(PlaywrightBrowser, "__init__", missing)
    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ConfigurationError, match=r"rastro-engine\[browser\]"):
        job_acquisition(job, None)
