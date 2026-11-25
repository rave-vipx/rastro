"""Origins through the real entry points: the CLI is the operator, MCP tools and registry
installs are untrusted, and only explicit grants widen an untrusted origin (offline)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro import AcquisitionPolicy, Outcome, parse_job, run_job_async
from rastro.application.acquisition import WebAcquirer
from rastro.application.capability_gate import CapabilityRefused
from rastro.application.operator_trust import current_origin, input_origin, set_process_trust
from rastro.domain.acquisition import HttpResponse
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

DENY_ALL = HttpResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /\n", 29)
STOP = "renderer-built-marker"


class _Stop(ConfigurationError):
    """Raised by the patched stealth renderer: the gate let the job through."""


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses or ("8.8.8.8",)

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return self.addresses


class Transport:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def get(self, target, **kwargs) -> HttpResponse:
        self.seen.append(target.url)
        if urlsplit(target.url).path == "/robots.txt":
            return DENY_ALL
        body = b"<p>ok</p>"
        return HttpResponse(200, {"content-type": "text/html"}, body, len(body))


def _job_text(*, render: str = "http", robots: str = "obey", urls=("https://example.com/",)) -> str:
    sources = "".join(f'[[sources]]\nurl = "{url}"\n' for url in urls)
    return (
        'format_version = 1\n[acquisition]\nallowed_hosts = ["example.com"]\n'
        f'min_interval_seconds = 0\nrender = "{render}"\nrobots = "{robots}"\n{sources}'
        '[recipe]\nstrategy = "css"\nrecord_selector = "p"\n[recipe.fields]\ntitle = "::text"\n'
        '[[fields]]\nname = "title"\ntype = "string"\n'
    )


@pytest.fixture
def built(monkeypatch):
    """Record each stealth renderer construction and stop there: no browser is started."""
    import rastro.adapters.scrapling_stealth as stealth

    for name in ("RASTRO_ALLOW_STEALTH", "RASTRO_ALLOW_CAPABILITIES", "RASTRO_ALLOW_UNTRUSTED"):
        monkeypatch.delenv(name, raising=False)
    constructed: list[str] = []

    def init(self) -> None:
        constructed.append("stealth")
        raise _Stop(STOP)

    monkeypatch.setattr(stealth.ScraplingStealth, "__init__", init)
    return constructed


def test_operator_job_stealth_runs_without_flag(built, tmp_path, capsys):
    from rastro.cli import main

    path = tmp_path / "job.toml"
    path.write_text(_job_text(render="stealth"), encoding="utf-8")
    assert main(["job", "run", str(path)]) != 0
    stderr = capsys.readouterr().err
    assert built == ["stealth"]
    assert STOP in stderr and "capability_refused" not in stderr


@pytest.mark.asyncio
async def test_mcp_job_stealth_refused_without_untrusted_grant(built, tmp_path):
    pytest.importorskip("mcp")
    from mcp.shared.memory import create_connected_server_and_client_session

    from rastro.adapters.mcp_server import build_server

    (tmp_path / "job.toml").write_text(_job_text(render="stealth"), encoding="utf-8")

    async def preview() -> tuple[bool, str]:
        server = build_server(tmp_path)._mcp_server
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool("job_extract", {"path": "job.toml"})
        return result.isError, result.content[0].text  # type: ignore[union-attr]

    failed, text = await preview()
    assert failed and json.loads(text)["error"] == "capability_refused"
    assert "--allow-untrusted mcp:stealth" in text and built == []
    previous = set_process_trust(OperatorTrust(untrusted_capabilities={"mcp": {"stealth"}}))
    try:
        failed, text = await preview()
    finally:
        set_process_trust(previous)
    assert failed and STOP in text and built == ["stealth"]


@pytest.mark.asyncio
async def test_bundle_cannot_enable_robots_ignore():
    job = parse_job(_job_text(robots="ignore"), format="toml")
    for origin in ("registry", "bundle"):
        transport = Transport()
        acquirer = WebAcquirer(job.acquisition, transport=transport, resolver=Resolver())
        with input_origin(origin), pytest.raises(CapabilityRefused) as caught:
            await run_job_async(job, acquisition=acquirer)
        assert caught.value.decision.refused == {"robots_ignore"}
        assert transport.seen == []


@pytest.mark.asyncio
async def test_a_policy_built_outside_a_job_is_refused_at_the_request():
    transport = Transport()
    policy = AcquisitionPolicy(allowed_hosts=("example.com",), robots="ignore")
    acquirer = WebAcquirer(policy, transport=transport, resolver=Resolver())
    from rastro import Source

    with input_origin("mcp"), pytest.raises(CapabilityRefused):
        await acquirer.acquire(Source(url="https://example.com/"), max_bytes=10_000)
    assert transport.seen == []


def test_registry_install_and_bundle_import_run_as_untrusted_origins(monkeypatch, tmp_path):
    import rastro.application.recipe_hub as hub
    import rastro.application.registry_install as install

    seen: list[str] = []

    def verify(*args, **kwargs):
        seen.append(current_origin())
        raise _Stop(STOP)

    monkeypatch.setattr(hub, "verify_bundle", verify)
    monkeypatch.setattr(install, "verify_bundle", verify)
    with pytest.raises(_Stop):
        install.install_package_dir(tmp_path, None, None, None, at=None)  # type: ignore[arg-type]
    monkeypatch.setattr(hub, "RecipeLifecycle", object)  # any repository passes the type check
    with pytest.raises(_Stop):
        hub.import_bundle(tmp_path, object(), None, at=None)  # type: ignore[arg-type]
    assert seen == ["registry", "bundle"]
    assert current_origin() == "operator"


@pytest.mark.asyncio
async def test_robots_ignore_from_operator_job_logged_once(caplog):
    urls = ("https://example.com/a", "https://example.com/b")
    job = parse_job(_job_text(robots="ignore", urls=urls), format="toml")
    transport = Transport()
    acquirer = WebAcquirer(job.acquisition, transport=transport, resolver=Resolver())
    with caplog.at_level(logging.INFO, logger="rastro"):
        report = await run_job_async(job, acquisition=acquirer)
    assert [r.status for r in report.results] == [Outcome.SUCCESS, Outcome.SUCCESS]
    assert all(urlsplit(url).path != "/robots.txt" for url in transport.seen)
    lines = [r.getMessage() for r in caplog.records if "robots_ignored" in r.getMessage()]
    assert len(lines) == 1 and "example.com/a" not in lines[0]


@pytest.mark.asyncio
async def test_private_network_still_needs_grant():
    job = parse_job(_job_text(robots="ignore"), format="toml")
    transport = Transport()
    acquirer = WebAcquirer(job.acquisition, transport=transport, resolver=Resolver("10.0.0.5"))
    report = await run_job_async(job, acquisition=acquirer)
    assert "destination_denied" in {issue.code for issue in report.results[0].issues}
    assert transport.seen == []


def test_allow_stealth_alias_deprecated_warning(capsys, monkeypatch):
    from rastro.cli import main

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    assert main(["job", "schema", "--allow-stealth"]) == 0
    warning = json.loads(capsys.readouterr().err.strip().splitlines()[0])
    assert warning["warning"] == "deprecated_flag" and "--allow-untrusted" in warning["hint"]
    assert main(["job", "schema"]) == 0
    assert "deprecated_flag" not in capsys.readouterr().err


def test_cli_grant_flags_are_validated_without_echo(capsys, tmp_path: Path):
    from rastro.cli import main

    secret = "s3cr3t-value-Qx7"
    for argv in (
        ["job", "schema", "--allow-capability", secret],
        ["job", "schema", "--allow-untrusted", f"mcp:{secret}"],
        ["job", "schema", "--allow-untrusted", "operator:stealth"],
    ):
        assert main(argv) != 0
        captured = capsys.readouterr()
        assert secret not in captured.out + captured.err


def test_model_proposals_get_only_the_model_grants(monkeypatch):
    import rastro.application.proposals as proposals
    from rastro.domain.intelligence import InferencePolicy, ModelError

    seen: list[str] = []

    def requests_steps(recipe) -> frozenset:
        seen.append(current_origin())
        return frozenset({"page_actions"})

    monkeypatch.setattr(proposals, "model_capabilities", requests_steps)
    output = json.dumps({"strategy": "css", "record_selector": "p", "fields": {"t": "::text"}})
    with pytest.raises(ModelError, match="invalid_model_response"):
        proposals.parse_proposal(output, InferencePolicy())
    previous = set_process_trust(OperatorTrust(untrusted_capabilities={"model": {"page_actions"}}))
    try:
        assert proposals.parse_proposal(output, InferencePolicy()).record_selector == "p"
    finally:
        set_process_trust(previous)
    assert seen == ["model", "model"]
