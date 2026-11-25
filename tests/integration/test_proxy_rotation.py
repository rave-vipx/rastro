"""Rotation over the operator's egress profiles: cyclic, redacted, gated like any proxy."""

import logging
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from rastro import AcquisitionPolicy, Outcome, Rastro, Source
from rastro.application.acquisition import WebAcquirer
from rastro.application.capability_gate import requested_capabilities
from rastro.application.run_report import report_for_run
from rastro.domain.acquisition import AcquisitionError, HttpResponse
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ConfigurationError

USERS = {"RASTRO_TEST_ROT_USER_A": "synthetic-user-a", "RASTRO_TEST_ROT_USER_B": "synthetic-user-b"}
PASSWORDS = {
    "RASTRO_TEST_ROT_PASS_A": "synthetic-pass-a",
    "RASTRO_TEST_ROT_PASS_B": "synthetic-pass-b",
}
ENVIRON = {**USERS, **PASSWORDS}
PROFILES = {
    "pool-a": {
        "proxy_url": "http://proxy-a.internal.example:3128",
        "username_env": "RASTRO_TEST_ROT_USER_A",
        "password_env": "RASTRO_TEST_ROT_PASS_A",
    },
    "pool-b": {
        "proxy_url": "http://proxy-b.internal.example:3128",
        "username_env": "RASTRO_TEST_ROT_USER_B",
        "password_env": "RASTRO_TEST_ROT_PASS_B",
    },
}
PAGE = (
    b'<script type="application/ld+json">'
    b'{"@type":"Product","name":"Synthetic lamp","offers":{"price":"12.50"}}</script>'
)


class Resolver:
    async def resolve(self, host, port):
        return ("8.8.8.8",)


def response(status=200, body=PAGE, **headers):
    return HttpResponse(status, {"content-type": "text/html", **headers}, body, len(body))


class Transport:
    def __init__(self, *, fail_proxy=()):
        self.calls = []
        self.fail_proxy = set(fail_proxy)

    async def get(self, target, **kwargs):
        self.calls.append((target, kwargs))
        proxy = kwargs.get("proxy")
        if proxy is not None and proxy.name in self.fail_proxy:
            raise AcquisitionError("proxy_failure", Outcome.TRANSIENT_FAILURE)
        if urlsplit(target.url).path == "/robots.txt":
            return response(404, b"")
        return response()


def policy(**changes):
    return AcquisitionPolicy(
        allowed_hosts=("example.com",),
        min_interval_seconds=0,
        backoff_seconds=0.001,
        egress_profiles=PROFILES,
        rotate_egress=("pool-a", "pool-b"),
        **changes,
    )


@pytest.mark.asyncio
async def test_rotation_cycles_profiles_and_redacts(schema, recipe, caplog, tmp_path):
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    transport = Transport()
    client = WebAcquirer(policy(), transport=transport, resolver=Resolver(), environ=ENVIRON)
    engine = Rastro(
        acquisition=client,
        policy=client.policy,
        repository=SQLiteRecipeRepository(tmp_path / "recipes.db"),
    )
    with caplog.at_level(logging.DEBUG):
        result = await engine.extract(
            source=Source(url="https://example.com/p"), schema=schema, recipe=recipe
        )
    assert result.status == Outcome.SUCCESS
    # robots.txt then the page: one transfer per profile, in declared order
    assert [options["proxy"].name for _, options in transport.calls] == ["pool-a", "pool-b"]
    assert transport.calls[1][1]["proxy"].password == "synthetic-pass-b"
    report = report_for_run(
        "job.toml", JobRunResult.from_results(schema_fingerprint="0" * 64, results=[result])
    )
    (source,) = report.sources
    assert source.acquisition is not None
    assert source.acquisition.proxy_rotation is True
    assert source.acquisition.proxies_used == 2
    artifacts = [report.model_dump_json(), result.model_dump_json(), repr(result), caplog.text]
    for secret in (*ENVIRON.values(), "proxy-a.internal.example", "proxy-b.internal.example"):
        assert all(secret not in artifact for artifact in artifacts)


@pytest.mark.asyncio
async def test_rotation_moves_to_the_next_profile_after_a_proxy_failure():
    transport = Transport(fail_proxy={"pool-a"})
    client = WebAcquirer(
        policy(max_retries=1), transport=transport, resolver=Resolver(), environ=ENVIRON
    )
    result = await client.acquire(Source(url="https://example.com/p"), max_bytes=5000)
    assert result.status == Outcome.SUCCESS
    names = [options["proxy"].name for _, options in transport.calls]
    assert names[:2] == ["pool-a", "pool-b"]  # the failed robots transfer retried on pool-b
    assert result.proxy_rotation and result.proxies_used == 2


@pytest.mark.asyncio
async def test_a_source_naming_its_own_egress_is_not_rotated():
    transport = Transport()
    client = WebAcquirer(policy(), transport=transport, resolver=Resolver(), environ=ENVIRON)
    result = await client.acquire(
        Source(url="https://example.com/p", egress="pool-b"), max_bytes=5000
    )
    assert {options["proxy"].name for _, options in transport.calls} == {"pool-b"}
    assert not result.proxy_rotation and result.proxies_used == 0


@pytest.mark.asyncio
async def test_missing_rotation_credentials_fail_before_any_request():
    transport = Transport()
    environ = {k: v for k, v in ENVIRON.items() if not k.endswith("_B")}
    client = WebAcquirer(policy(), transport=transport, resolver=Resolver(), environ=environ)
    result = await client.acquire(Source(url="https://example.com/p"), max_bytes=5000)
    assert result.issue_code == "egress_credentials_unavailable"
    assert transport.calls == []


@pytest.mark.parametrize(
    "rotate",
    [("pool-a", "missing"), ("pool-a", "pool-a"), ("direct",), ()],
)
def test_rotation_names_must_be_distinct_declared_profiles(rotate):
    if rotate == ():
        assert AcquisitionPolicy(allowed_hosts=("example.com",)).rotate_egress == ()
        return
    with pytest.raises(ValidationError):
        AcquisitionPolicy(
            allowed_hosts=("example.com",), egress_profiles=PROFILES, rotate_egress=rotate
        )


def test_rotation_requests_the_proxy_rotation_capability():
    from rastro.domain.jobs import parse_job

    job = parse_job(_job_toml(), format="toml")
    assert "proxy_rotation" in requested_capabilities(job)


def _job_toml() -> str:
    profiles = "".join(
        f'[acquisition.egress_profiles.{name}]\nproxy_url = "{p["proxy_url"]}"\n'
        f'username_env = "{p["username_env"]}"\npassword_env = "{p["password_env"]}"\n'
        for name, p in PROFILES.items()
    )
    return f"""format_version = 1
discover_recipes = true
[acquisition]
allowed_hosts = ["example.com"]
rotate_egress = ["pool-a", "pool-b"]
{profiles}
[[sources]]
url = "https://example.com/"
[[fields]]
name = "name"
type = "string"
"""


@pytest.mark.usefixtures("offline_cli_loop")
def test_rotation_requires_allow_egress(tmp_path: Path, monkeypatch):
    from rastro import run_job_file

    monkeypatch.delenv("RASTRO_ALLOW_EGRESS", raising=False)
    for name, value in ENVIRON.items():
        monkeypatch.setenv(name, value)
    job = tmp_path / "job.toml"
    job.write_text(_job_toml(), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="allow-egress"):
        run_job_file(job)


@pytest.mark.parametrize("hosts", [{"other.example": {}}, {"example.com": {"max_concurrency": 0}}])
def test_host_limits_need_an_allowed_host(hosts):
    with pytest.raises(ValidationError):
        AcquisitionPolicy(allowed_hosts=("example.com",), hosts=hosts)


def test_host_limits_normalize_host_names():
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",), hosts={"EXAMPLE.com": {"min_interval_seconds": 2}}
    )
    assert policy.hosts["example.com"].min_interval_seconds == 2
