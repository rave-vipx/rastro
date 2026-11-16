"""Operator network permissions through the acquirer and the job entry points (offline).

Requirement IDs: SEC-001, SEC-002, SEC-003, SEC-004, SEC-011.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import pytest

from rastro import AcquisitionPolicy, ConfigurationError, Outcome, Source, parse_job, run_job_async
from rastro.application.acquisition import WebAcquirer
from rastro.application.operator_trust import operator_trust
from rastro.domain.acquisition import HttpResponse

SECRET = "s3cr3t-token-value-7Hq"


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses or ("8.8.8.8",)

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return self.addresses


class Transport:
    """Records (url, address, headers) per request; ``redirects`` maps a path to a Location."""

    def __init__(self, *, robots: HttpResponse | None = None, redirects=None, page_status=200):
        self.robots = robots or HttpResponse(404, {"content-type": "text/plain"}, b"", 0)
        self.redirects = redirects or {}
        self.page_status = page_status
        self.seen: list[tuple[str, str, dict[str, str]]] = []

    async def get(self, target, **kwargs) -> HttpResponse:
        self.seen.append((target.url, target.address, dict(kwargs["headers"])))
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            return self.robots
        if path in self.redirects:
            return HttpResponse(302, {"location": self.redirects[path]}, b"", 0)
        body = b"<p>ok</p>"
        return HttpResponse(self.page_status, {"content-type": "text/html"}, body, len(body))

    def pages(self) -> list[tuple[str, str, dict[str, str]]]:
        return [item for item in self.seen if urlsplit(item[0]).path != "/robots.txt"]


def _acquirer(transport, resolver=None, environ=None, **policy) -> WebAcquirer:
    policy.setdefault("allowed_hosts", ("example.com",))
    return WebAcquirer(
        AcquisitionPolicy(min_interval_seconds=0, max_retries=0, **policy),
        transport=transport,
        resolver=resolver or Resolver(),
        environ=environ,
    )


async def _get(acquirer, url="https://example.com/"):
    return await acquirer.acquire(Source(url=url), max_bytes=10_000)


@pytest.mark.asyncio
async def test_private_network_is_refused_by_default_and_allowed_with_the_grant():
    transport = Transport()
    result = await _get(_acquirer(transport, Resolver("10.0.0.5")))
    assert (result.status, result.issue_code) == (Outcome.ACCESS_RESTRICTION, "destination_denied")
    assert transport.seen == []
    with operator_trust(allow_private_network=True):
        result = await _get(_acquirer(transport, Resolver("10.0.0.5")))
        assert result.status == Outcome.SUCCESS
        assert transport.seen[0][1] == "10.0.0.5"
        metadata = await _get(_acquirer(Transport(), Resolver("169.254.169.254")))
        assert metadata.issue_code == "destination_denied"
        mixed = await _get(_acquirer(Transport(), Resolver("10.0.0.5", "169.254.169.254")))
        assert mixed.issue_code == "destination_denied"


@pytest.mark.asyncio
async def test_a_private_redirect_target_is_checked_per_hop():
    transport = Transport(redirects={"/": "https://other.example/"})
    acquirer = _acquirer(transport, allowed_hosts=("example.com", "other.example"))

    class Split(Resolver):
        async def resolve(self, host: str, port: int) -> tuple[str, ...]:
            return ("169.254.169.254",) if host == "other.example" else ("10.0.0.5",)

    acquirer._resolver = Split()
    with operator_trust(allow_private_network=True):
        result = await _get(acquirer)
    assert result.issue_code == "destination_denied"
    assert [item[0] for item in transport.seen] == [
        "https://example.com/robots.txt",
        "https://example.com/",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://example.com:8443/", "http://example.com:8443/"])
async def test_extra_ports_need_the_grant(url):
    transport = Transport()
    acquirer = _acquirer(transport, allow_http=True)
    refused = await _get(acquirer, url)
    assert refused.issue_code == "port_not_allowed"
    with operator_trust(allow_ports=[8443]):
        assert (await _get(acquirer, url)).status == Outcome.SUCCESS
        other = await _get(acquirer, url.replace("8443", "9000"))
        assert other.issue_code == "port_not_allowed"
    assert transport.seen[0][0].startswith(url.rsplit("/", 1)[0])


DENY_ALL = HttpResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /\n", 29)


@pytest.mark.asyncio
async def test_robots_override_skips_robots_for_that_host_only(caplog):
    transport = Transport(robots=DENY_ALL)
    acquirer = _acquirer(transport)
    refused = await _get(acquirer)
    assert refused.issue_code == "robots_denied" and not refused.robots_overridden
    transport.seen.clear()
    with caplog.at_level(logging.INFO, logger="rastro"):
        with operator_trust(ignore_robots=["other.example"]):
            assert (await _get(acquirer)).issue_code == "robots_denied"
        with operator_trust(ignore_robots=["example.com"]):
            result = await _get(acquirer)
    assert result.status == Outcome.SUCCESS and result.robots_overridden is True
    assert all(urlsplit(item[0]).path != "/robots.txt" for item in transport.seen[-1:])
    lines = [r.getMessage() for r in caplog.records if "robots_overridden" in r.getMessage()]
    assert len(lines) == 1 and "example.com" in lines[0] and "http" not in lines[0]


@pytest.mark.asyncio
async def test_robots_override_keeps_pacing_and_restriction_stops():
    transport = Transport(page_status=403)
    acquirer = _acquirer(transport)
    with operator_trust(ignore_robots=["example.com"]):
        first = await _get(acquirer)
        second = await _get(acquirer)
    assert first.issue_code == "access_restricted"
    assert second.issue_code == "host_access_restricted"
    assert len(transport.pages()) == 1


def _auth(**entry) -> dict:
    return {
        "allowed_hosts": ("example.com", "other.example"),
        "auth": [{"host": "example.com", "value_env": "RASTRO_TOKEN", "scheme": "Bearer", **entry}],
    }


@pytest.mark.asyncio
async def test_auth_header_reaches_only_the_page_request_on_its_host(caplog):
    transport = Transport(redirects={"/jump": "https://other.example/landing"})
    acquirer = _acquirer(transport, environ={"RASTRO_TOKEN": SECRET}, **_auth())
    with caplog.at_level(logging.DEBUG, logger="rastro"):
        ok = await _get(acquirer)
        jumped = await _get(acquirer, "https://example.com/jump")
    assert ok.status == jumped.status == Outcome.SUCCESS
    by_url = {url: headers for url, _, headers in transport.seen}
    assert by_url["https://example.com/"]["Authorization"] == f"Bearer {SECRET}"
    assert "Authorization" not in by_url["https://example.com/robots.txt"]
    assert "Authorization" not in by_url["https://other.example/landing"]
    assert "Authorization" not in by_url["https://other.example/robots.txt"]
    for text in (repr(ok), repr(acquirer.policy), repr(acquirer), caplog.text):
        assert SECRET not in text


@pytest.mark.asyncio
async def test_auth_header_custom_name_without_scheme():
    transport = Transport()
    acquirer = _acquirer(
        transport,
        environ={"RASTRO_TOKEN": SECRET},
        allowed_hosts=("example.com",),
        auth=[{"host": "example.com", "header": "X-Api-Key", "value_env": "RASTRO_TOKEN"}],
    )
    await _get(acquirer)
    assert transport.pages()[0][2]["X-Api-Key"] == SECRET


@pytest.mark.asyncio
async def test_missing_or_invalid_auth_value_is_an_explicit_failure_without_echo():
    transport = Transport()
    for environ in ({}, {"RASTRO_TOKEN": ""}, {"RASTRO_TOKEN": f"{SECRET}\r\nX: y"}):
        result = await _get(_acquirer(transport, environ=environ, **_auth()))
        assert (result.status, result.issue_code) == (
            Outcome.PERMANENT_FAILURE,
            "auth_unavailable",
        )
        assert SECRET not in repr(result) and "RASTRO_TOKEN" not in repr(result)
    assert transport.seen == []


@pytest.mark.asyncio
async def test_auth_needs_https_unless_loopback_with_the_private_grant():
    transport = Transport()
    entry = {"host": "example.com", "value_env": "RASTRO_TOKEN"}
    acquirer = _acquirer(transport, environ={"RASTRO_TOKEN": SECRET}, allow_http=True, auth=[entry])
    refused = await _get(acquirer, "http://example.com/")
    assert refused.issue_code == "auth_requires_https"
    assert transport.pages() == []
    local = _acquirer(
        transport,
        Resolver("127.0.0.1"),
        {"RASTRO_TOKEN": SECRET},
        allowed_hosts=("127.0.0.1",),
        allow_http=True,
        auth=[{**entry, "host": "127.0.0.1"}],
    )
    with operator_trust(allow_ports=[8080]):
        assert (await _get(local, "http://127.0.0.1:8080/")).issue_code == "destination_denied"
    with operator_trust(allow_ports=[8080], allow_private_network=True):
        assert (await _get(local, "http://127.0.0.1:8080/")).status == Outcome.SUCCESS
    assert transport.pages()[0][2]["Authorization"] == SECRET


def _job(extra: str = "", acquisition: str = "", url: str = "https://example.com/") -> str:
    return f"""format_version = 1
[acquisition]
allowed_hosts = ["example.com"]
min_interval_seconds = 0
{acquisition}
[[acquisition.auth]]
host = "example.com"
value_env = "{extra or "RASTRO_TOKEN"}"
scheme = "Bearer"
[[sources]]
url = "{url}"
[recipe]
strategy = "jsonld"
item_type = "Product"
[recipe.fields]
name = "/name"
[[fields]]
name = "name"
type = "string"
"""


async def _run(text: str, monkeypatch, value: str | None = SECRET, **options):
    if value is None:
        monkeypatch.delenv("RASTRO_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("RASTRO_TOKEN", value)
        monkeypatch.setenv("GITHUB_TOKEN", value)
    job = parse_job(text, format="toml")
    return await run_job_async(job, acquisition=_acquirer(Transport()), **options)


@pytest.mark.asyncio
async def test_job_auth_passes_load_checks_with_a_rastro_variable(monkeypatch):
    report = await _run(_job(), monkeypatch)
    assert report.results[0].source_url == "https://example.com/"


@pytest.mark.asyncio
async def test_job_auth_refuses_names_outside_the_rule_without_echo(monkeypatch):
    with pytest.raises(ConfigurationError) as caught:
        await _run(_job("GITHUB_TOKEN"), monkeypatch)
    assert "GITHUB_TOKEN" not in str(caught.value) and SECRET not in str(caught.value)
    await _run(_job("GITHUB_TOKEN"), monkeypatch, allow_env=["GITHUB_TOKEN"])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "bad\r\nvalue"])
async def test_job_auth_refuses_a_missing_or_malformed_value_without_echo(monkeypatch, value):
    with pytest.raises(ConfigurationError) as caught:
        await _run(_job(), monkeypatch, value)
    assert "RASTRO_TOKEN" not in str(caught.value) and "bad" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "acquisition", ['render = "browser"', 'render = "stealth"', "solve_challenges = true"]
)
async def test_job_auth_is_http_engine_only(monkeypatch, acquisition):
    with pytest.raises(ConfigurationError, match="HTTP-engine only"):
        await _run(_job(acquisition=acquisition), monkeypatch, allow_stealth=True)


@pytest.mark.asyncio
async def test_job_auth_refuses_an_http_source_unless_loopback_with_the_grant(monkeypatch):
    plain = _job(acquisition="allow_http = true", url="http://example.com/")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        await _run(plain, monkeypatch)
    with pytest.raises(ConfigurationError, match="HTTPS"):
        await _run(plain, monkeypatch, allow_private_network=True)
    local = (
        _job(acquisition="allow_http = true", url="http://127.0.0.1:8080/")
        .replace('host = "example.com"', 'host = "127.0.0.1"')
        .replace('allowed_hosts = ["example.com"]', 'allowed_hosts = ["127.0.0.1"]')
    )
    with pytest.raises(ConfigurationError, match="HTTPS"):
        await _run(local, monkeypatch)
    await _run(local, monkeypatch, allow_private_network=True, allow_ports=[8080])


def test_conditional_digest_and_job_dump_hold_names_never_values(monkeypatch):
    """SYNC-004/SEC-004: the digest reads the job, which names the variable but not its value."""
    from rastro.application.conditional_inputs import extraction_inputs

    job = parse_job(_job(), format="toml")
    monkeypatch.setenv("RASTRO_TOKEN", SECRET)
    first = extraction_inputs(job, base_dir=None, allow_absolute_paths=False)
    monkeypatch.setenv("RASTRO_TOKEN", "another-value")
    assert extraction_inputs(job, base_dir=None, allow_absolute_paths=False) == first
    assert SECRET not in job.model_dump_json() and SECRET not in repr(job)


def test_the_browser_engine_refuses_auth_headers_defensively():
    from rastro.application.browser_acquisition import BrowserAcquirer

    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",),
        render="browser",
        auth=[{"host": "example.com", "value_env": "RASTRO_TOKEN"}],
    )
    with pytest.raises(ConfigurationError, match="HTTP-engine only"):
        BrowserAcquirer(policy, renderer=None, transport=None, resolver=None)  # type: ignore[arg-type]
