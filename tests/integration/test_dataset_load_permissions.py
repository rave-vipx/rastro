"""A dataset load honours the operator's network grants and auth rules (offline).

Requirement IDs: SCHED-001, SYNC-001, SEC-001, SEC-004.
"""

from __future__ import annotations

import pytest

from rastro import ConfigurationError
from rastro.adapters.dns import SystemResolver
from rastro.adapters.http import CurlTransport
from rastro.application.job_dataset import load_job_file
from rastro.domain.acquisition import HttpResponse

SECRET = "s3cr3t-token-value-7Hq"
JOB = """format_version = 1
[[fields]]
name = "title"
type = "string"
[[sources]]
kind = "url"
url = "http://127.0.0.1:8080/list"
[recipe]
strategy = "css"
record_selector = "article"
[recipe.fields]
title = "h1::text"
[acquisition]
allowed_hosts = ["127.0.0.1"]
allow_http = true
min_interval_seconds = 0
[[acquisition.auth]]
host = "127.0.0.1"
value_env = "{env}"
scheme = "Bearer"
[dataset]
name = "d"
path = "d.sqlite"
identity = ["title"]
"""


@pytest.fixture
def requests(monkeypatch, offline_cli_loop):
    seen: list[tuple[str, dict[str, str]]] = []

    async def resolve(self, host, port):
        return ("127.0.0.1",)

    async def get(self, target, *, headers, **kwargs):
        seen.append((target.url, dict(headers)))
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        body = b"<article><h1>one</h1></article>"
        return HttpResponse(200, {"content-type": "text/html"}, body, len(body))

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    monkeypatch.setenv("RASTRO_TOKEN", SECRET)
    monkeypatch.setenv("OTHER_TOKEN", SECRET)
    return seen


def _job(tmp_path, env: str = "RASTRO_TOKEN"):
    path = tmp_path / "job.toml"
    path.write_text(JOB.format(env=env))
    return path


def test_a_load_fetches_from_the_granted_port_and_private_address(tmp_path, requests):
    path = _job(tmp_path)
    denied = load_job_file(path, allow_private_network=True)  # port 8080 not granted
    assert denied.exit_code != 0 and not [r for r in requests if r[0].endswith("/list")]
    loaded = load_job_file(path, allow_private_network=True, allow_ports=[8080])
    assert loaded.exit_code == 0
    pages = [r for r in requests if r[0].endswith("/list")]
    assert [p[0] for p in pages] == ["http://127.0.0.1:8080/list"]
    assert pages[0][1]["Authorization"] == f"Bearer {SECRET}"


def test_a_load_refuses_an_auth_env_name_outside_the_rule_before_any_request(tmp_path, requests):
    path = _job(tmp_path, "OTHER_TOKEN")
    with pytest.raises(ConfigurationError) as caught:
        load_job_file(path, allow_private_network=True, allow_ports=[8080])
    assert "OTHER_TOKEN" not in str(caught.value) and SECRET not in str(caught.value)
    assert requests == []
    assert (
        load_job_file(
            path, allow_private_network=True, allow_ports=[8080], allow_env=["OTHER_TOKEN"]
        ).exit_code
        == 0
    )


def test_a_watch_tick_passes_the_operator_grants_to_the_load(tmp_path, requests):
    from rastro.application.scheduler import watch_job

    path = tmp_path / "job.toml"
    path.write_text(JOB.format(env="RASTRO_TOKEN") + '[schedule]\nevery = "1h"\n')
    code = watch_job(path, once=True, allow_private_network=True, allow_ports=[8080])
    assert code == 0
    assert [r[0] for r in requests if r[0].endswith("/list")] == ["http://127.0.0.1:8080/list"]
