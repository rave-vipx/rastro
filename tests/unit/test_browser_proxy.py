"""Browser and stealth modes may use egress profiles; target checks stay on the target."""

from unittest.mock import AsyncMock

import pytest

from rastro import Rastro
from rastro.application.browser_proxy import BROWSER_PROXY
from rastro.application.run_report import report_for_run
from rastro.domain.acquisition import (
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
    ProxyRoute,
)
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import Outcome, Source

ENVIRON = {"RASTRO_TEST_BROWSER_PROXY_USER": "synthetic-user"}
PROFILES = {
    "relay": {
        "proxy_url": "http://relay.internal.example:3128",
        "username_env": "RASTRO_TEST_BROWSER_PROXY_USER",
    }
}
PAGE = (
    '<script type="application/ld+json">'
    '{"@type":"Product","name":"Synthetic lamp","offers":{"price":"12.50"}}</script>'
)
SOURCE = Source(url="https://example.com/", egress="relay")
TARGET = PinnedTarget("https://example.com/", "example.com", 443, "93.184.216.34")


class Renderer:
    def __init__(self, **result):
        self.proxies: list[ProxyRoute | None] = []
        self.result = result

    async def render(self, target, *, policy, max_bytes, targets):
        self.proxies.append(BROWSER_PROXY.get())
        return AcquisitionResult(
            target.url, PAGE, Outcome.SUCCESS, None, http_status=200, requests=1, **self.result
        )


def client(renderer, *, mode="browser", answers=("93.184.216.34",), **changes):
    from rastro.application.browser_acquisition import BrowserAcquirer

    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",),
        render=mode,
        min_interval_seconds=0,
        egress_profiles=PROFILES,
        **changes,
    )
    transport = AsyncMock()
    transport.get.return_value = HttpResponse(404, {}, b"", 0)
    resolver = AsyncMock()
    resolver.resolve.return_value = answers
    acquirer = BrowserAcquirer(
        policy, renderer=renderer, transport=transport, resolver=resolver, environ=ENVIRON
    )
    return acquirer, resolver


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["browser", "stealth"])
async def test_browser_mode_with_proxy_keeps_ssrf_on_target(mode):
    renderer = Renderer()
    acquirer, resolver = client(renderer, mode=mode)
    result = await acquirer.acquire(
        Source(url="https://example.com/", egress="relay"), max_bytes=5000
    )
    assert result.status == Outcome.SUCCESS and result.egress == "relay"
    (route,) = renderer.proxies
    assert route is not None and route.name == "relay" and route.username == "synthetic-user"
    assert {call.args[0] for call in resolver.resolve.await_args_list} == {"example.com"}

    # a target that resolves to a private address is refused before the browser starts
    denied = Renderer()
    acquirer, _ = client(denied, mode=mode, answers=("127.0.0.1",))
    result = await acquirer.acquire(
        Source(url="https://example.com/", egress="relay"), max_bytes=5000
    )
    assert result.issue_code == "destination_denied" and denied.proxies == []


def test_browser_flags_route_through_the_proxy_and_guard_the_target():
    from rastro.adapters.playwright_browser import browser_flags, proxy_option, route_allowed

    route = ProxyRoute("relay", "http://relay.internal.example:3128", "u", "p")
    flags = browser_flags((TARGET,), route)
    assert "--no-proxy-server" not in flags
    rules = next(flag for flag in flags if flag.startswith("--host-resolver-rules="))
    assert "EXCLUDE relay.internal.example" in rules and "MAP * ~NOTFOUND" in rules
    assert "--no-proxy-server" in browser_flags((TARGET,))
    assert proxy_option(route) == {
        "server": "http://relay.internal.example:3128",
        "username": "u",
        "password": "p",
    }
    assert proxy_option(None) is None
    policy = AcquisitionPolicy(allowed_hosts=("example.com",))
    # the guard judges each request by its target, never by the proxy hop
    assert not route_allowed("http://relay.internal.example:3128/", "document", policy)
    assert route_allowed("https://example.com/a.js", "script", policy)


def test_stealth_options_carry_the_proxy():
    from rastro.adapters.playwright_browser import NetworkGuard
    from rastro.adapters.scrapling_stealth import _options

    policy = AcquisitionPolicy(allowed_hosts=("example.com",))
    route = ProxyRoute("relay", "http://relay.internal.example:3128")
    options = _options(NetworkGuard(policy), policy, (TARGET,), route)
    assert options["proxy"] == {"server": "http://relay.internal.example:3128"}
    assert "--no-proxy-server" not in options["extra_flags"]
    assert "proxy" not in _options(NetworkGuard(policy), policy, (TARGET,), None)


def test_job_browser_modes_accept_egress_profiles():
    from rastro.browser import job_render_modes
    from rastro.domain.jobs import Job

    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "discover_recipes": True,
            "region": {"egress": "relay"},
            "sources": [{"url": "https://example.com/a"}],
            "acquisition": {
                "allowed_hosts": ["example.com"],
                "render": "stealth",
                "egress_profiles": {"relay": "http://relay.internal.example:3128"},
            },
        }
    )
    assert job_render_modes(job) == {"https://example.com/a": "stealth"}


@pytest.mark.asyncio
async def test_guard_notices_a_challenge_that_was_cleared():
    from rastro.adapters.playwright_browser import NetworkGuard

    guard = NetworkGuard(AcquisitionPolicy(allowed_hosts=("example.com",)))
    page = AsyncMock()
    session = AsyncMock()
    handlers = {}
    session.on = lambda event, callback: handlers.update({event: callback})
    page.context.new_cdp_session.return_value = session
    await guard.setup(page)
    await handlers["Network.responseReceived"](
        {"type": "Document", "response": {"status": 403, "headers": {"CF-Mitigated": "challenge"}}}
    )
    result = guard.result("https://example.com/", "<p>Welcome</p>", 200, 1000)
    assert result.challenge_solved is True
    guard.begin(guard.policy)
    assert guard.result("https://example.com/", "<p>x</p>", 200, 1000).challenge_solved is False


@pytest.mark.asyncio
async def test_challenge_solved_reported(schema, recipe):
    acquirer, _ = client(Renderer(challenge_solved=True), mode="stealth", solve_challenges=True)
    engine = Rastro(acquisition=acquirer, policy=acquirer.policy)
    result = await engine.extract(
        source=Source(url="https://example.com/"), schema=schema, recipe=recipe
    )
    assert result.status == Outcome.SUCCESS
    report = report_for_run(
        "job.toml", JobRunResult.from_results(schema_fingerprint="0" * 64, results=[result])
    )
    (source,) = report.sources
    assert source.acquisition is not None
    assert source.acquisition.challenge_solved is True
    assert source.acquisition.render == "stealth"
