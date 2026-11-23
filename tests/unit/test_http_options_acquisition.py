"""The job's HTTP options reach the transport; redirect and capability checks stay in force."""

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.capability_gate import CapabilityRefused, require_capabilities
from rastro.application.operator_trust import input_origin, set_process_trust
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse
from rastro.domain.browser_controls import HttpOptions
from rastro.domain.jobs import Job
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import Outcome, Source


class Resolver:
    async def resolve(self, host, port):
        return {"shop.example": ("8.8.8.8",), "other.example": ("8.8.4.4",)}.get(
            host, ("10.0.0.7",)
        )


class Transport:
    def __init__(self, location: str | None = None):
        self.location = location
        self.calls: list[dict] = []

    async def get(
        self, target, *, headers, timeout, max_bytes, proxy, impersonate=None, http_version=None
    ):
        self.calls.append(
            {"url": target.url, "impersonate": impersonate, "http_version": http_version}
        )
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        if self.location and target.url.endswith("/start"):
            return HttpResponse(302, {"location": self.location}, b"", 0)
        return HttpResponse(200, {"content-type": "text/html"}, b"<p>x</p>", 8)


def _acquirer(transport, http: HttpOptions, hosts=("shop.example",)):
    policy = AcquisitionPolicy(
        allowed_hosts=hosts, min_interval_seconds=0, max_retries=0, http=http
    )
    return WebAcquirer(policy, transport=transport, resolver=Resolver())


@pytest.mark.asyncio
async def test_http_options_reach_every_transfer():
    transport = Transport()
    client = _acquirer(transport, HttpOptions(impersonate="chrome", http_version="3"))
    result = await client.acquire(Source(url="https://shop.example/start"), max_bytes=1000)
    assert result.status == Outcome.SUCCESS
    assert transport.calls and all(
        call["impersonate"] == "chrome" and call["http_version"] == "3" for call in transport.calls
    )


@pytest.mark.asyncio
async def test_defaults_leave_the_transport_on_its_plain_profile():
    transport = Transport()
    await _acquirer(transport, HttpOptions()).acquire(
        Source(url="https://shop.example/a"), max_bytes=1000
    )
    assert {(c["impersonate"], c["http_version"]) for c in transport.calls} == {(None, None)}


@pytest.mark.asyncio
async def test_impersonation_alone_lets_the_browser_choose_the_protocol():
    transport = Transport()
    await _acquirer(transport, HttpOptions(impersonate="firefox")).acquire(
        Source(url="https://shop.example/a"), max_bytes=1000
    )
    assert {(c["impersonate"], c["http_version"]) for c in transport.calls} == {("firefox", None)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "code", "hosts"),
    [
        ("https://elsewhere.example/x", "host_not_allowed", ("shop.example",)),
        ("http://shop.example/x", "https_required", ("shop.example",)),
        ("https://inside.example/x", "destination_denied", ("shop.example", "inside.example")),
    ],
)
async def test_redirect_checks_unchanged_with_impersonation(location, code, hosts):
    transport = Transport(location=location)
    client = _acquirer(transport, HttpOptions(impersonate="chrome", http_version="3"), hosts)
    result = await client.acquire(Source(url="https://shop.example/start"), max_bytes=1000)
    assert result.issue_code == code
    assert result.body is None
    assert not any("/x" in call["url"] for call in transport.calls)


def _job() -> Job:
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://shop.example/"}],
            "fields": [{"name": "title", "type": "string"}],
            "recipe": {"strategy": "css", "record_selector": "p", "fields": {"title": "::text"}},
            "acquisition": {
                "allowed_hosts": ["shop.example"],
                "http": {"impersonate": "chrome", "http_version": "3"},
            },
        }
    )


@pytest.fixture
def no_env_grants(monkeypatch):
    for name in ("RASTRO_ALLOW_STEALTH", "RASTRO_ALLOW_CAPABILITIES", "RASTRO_ALLOW_UNTRUSTED"):
        monkeypatch.delenv(name, raising=False)


def test_impersonation_allowed_for_operator_jobs_by_default(no_env_grants):
    decision = require_capabilities(_job())
    assert {"impersonation", "http3"} <= decision.granted


def test_impersonation_refused_for_mcp_origin_default(no_env_grants):
    with input_origin("mcp"), pytest.raises(CapabilityRefused) as caught:
        require_capabilities(_job())
    assert {"impersonation", "http3"} <= set(caught.value.decision.refused)
    assert "--allow-untrusted mcp:impersonation" in (caught.value.hint or "")


def test_impersonation_granted_to_mcp_only_by_the_operator(no_env_grants):
    grants = {"mcp": {"impersonation", "http3"}}
    previous = set_process_trust(OperatorTrust(untrusted_capabilities=grants))
    try:
        with input_origin("mcp"):
            assert {"impersonation", "http3"} <= require_capabilities(_job()).granted
    finally:
        set_process_trust(previous)


class Negotiated(Transport):
    async def get(self, target, **kwargs):
        response = await super().get(target, **kwargs)
        if target.url.endswith("robots.txt"):
            return response
        return HttpResponse(
            response.status,
            response.headers,
            response.body,
            response.bytes_received,
            http_version="2",
            fallbacks=("http3_unavailable",),
        )


def test_negotiated_version_and_fallbacks_reach_the_report(offline_cli_loop):
    from rastro.application.jobs import run_job

    spec = _job()  # asks for HTTP/3 and chrome impersonation
    client = WebAcquirer(spec.acquisition, transport=Negotiated(), resolver=Resolver())
    run = run_job(spec, acquisition=client)
    result = run.results[0]
    assert result.notes.acquisition.http_version == "2"
    assert "http3_unavailable" in {issue.code for issue in result.issues}
    assert run.report.sources[0].acquisition.http_version == "2"
