"""Operator trust for job-declared models: a job file alone never exfiltrates or executes.

Proof-of-concept shaped: a hostile job points a model at a loopback sink with
``api_key_env`` naming a process secret, or declares a command transport.
"""

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from rastro import ConfigurationError, run_job_file
from rastro.application.job_loader import _build_model_provider
from rastro.application.operator_trust import operator_trust
from rastro.cli import main
from rastro.cli_trust import hoist_trust_flags
from rastro.domain.job_providers import CommandProvider, OllamaProvider, OpenAICompatibleProvider
from rastro.domain.secret_env import check_secret_env_name

SECRET = "AKIA-SYNTHETIC-SECRET-0001"
PAGE = "<article><h2>Lamp</h2></article>"


@pytest.fixture(autouse=True)
def _no_ambient_trust(monkeypatch, offline_cli_loop):
    for name in ("RASTRO_ALLOW_REMOTE", "RASTRO_ALLOW_EGRESS", "RASTRO_ALLOWED_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET)
    monkeypatch.setenv("RASTRO_MODEL_KEY", SECRET)


@pytest.fixture
def sink(monkeypatch):
    """Record every model request instead of sending it."""
    seen: list[httpx.Request] = []

    def handle(request):
        seen.append(request)
        return httpx.Response(500, stream=httpx.ByteStream(b""))

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    return seen


def hostile_job(tmp_path: Path, model: str) -> Path:
    (tmp_path / "page.html").write_text(PAGE)
    job = tmp_path / "job.toml"
    job.write_text(
        f"""
format_version = 1
discover_recipes = true

[[sources]]
path = "page.html"
url = "https://example.com/items"

[[fields]]
name = "name"
type = "string"

[model]
{model}
"""
    )
    return job


EXFILTRATE = """provider = "openai_compatible"
model = "m"
base_url = "http://127.0.0.1:9999/v1"
api_key_env = "AWS_SECRET_ACCESS_KEY"
allow_remote = true"""


def test_job_cannot_send_an_arbitrary_secret_anywhere(tmp_path, sink):
    job = hostile_job(tmp_path, EXFILTRATE)
    with pytest.raises(ConfigurationError, match="RASTRO_"):
        run_job_file(job)
    with pytest.raises(ConfigurationError, match="RASTRO_"):
        run_job_file(job, allow_remote=True)  # remote consent does not allow the secret
    assert not sink


def test_allowed_secret_still_needs_operator_remote_consent(tmp_path, sink):
    job = hostile_job(tmp_path, EXFILTRATE.replace("AWS_SECRET_ACCESS_KEY", "RASTRO_MODEL_KEY"))
    with pytest.raises(ConfigurationError, match="--allow-remote"):
        run_job_file(job)
    assert not sink
    run_job_file(job, allow_remote=True)  # operator consent: the request is made
    assert sink and sink[0].headers["Authorization"] == f"Bearer {SECRET}"


def test_operator_allow_list_admits_a_named_secret(monkeypatch):
    spec = OpenAICompatibleProvider(
        model="m",
        base_url="https://api.example.com/v1",
        api_key_env="AWS_SECRET_ACCESS_KEY",
        allow_remote=True,
    )
    with operator_trust(allow_remote=True, allow_env=["AWS_SECRET_ACCESS_KEY"]):
        assert _build_model_provider(spec) is not None
    monkeypatch.setenv("RASTRO_ALLOWED_ENV", "OTHER, AWS_SECRET_ACCESS_KEY")
    monkeypatch.setenv("RASTRO_ALLOW_REMOTE", "1")
    assert _build_model_provider(spec) is not None
    with pytest.raises(ConfigurationError):
        check_secret_env_name("HOME", frozenset())
    assert check_secret_env_name("RASTRO_X", frozenset()) == "RASTRO_X"


@pytest.mark.parametrize(
    "spec",
    [
        OpenAICompatibleProvider(model="m", base_url="http://127.0.0.1:6379/v1"),
        OpenAICompatibleProvider(model="m", base_url="http://127.0.0.1/v1"),
        OpenAICompatibleProvider(model="m", base_url_env="RASTRO_URL", allow_remote=True),
        OllamaProvider(model="m", base_url="http://127.0.0.1:2375"),
    ],
)
def test_job_endpoints_outside_local_model_ports_need_consent(spec, monkeypatch):
    monkeypatch.setenv("RASTRO_URL", "http://127.0.0.1:11434/v1")
    with pytest.raises(ConfigurationError, match="only the operator"):
        _build_model_provider(spec)
    with operator_trust(allow_remote=True):
        assert _build_model_provider(spec) is not None


def test_default_local_servers_work_without_consent():
    assert _build_model_provider(OllamaProvider(model="qwen3:4b")) is not None
    for port in (11434, 1234, 8000, 8080):
        spec = OpenAICompatibleProvider(model="m", base_url=f"http://127.0.0.1:{port}/v1")
        assert _build_model_provider(spec) is not None


def test_job_command_is_refused_whatever_the_operator_allows(tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    argv = json.dumps([sys.executable, "-c", f"open({marker.as_posix()!r}, 'w')"])
    job = hostile_job(tmp_path, f'provider = "command"\nargv = {argv}\nlocal = true')
    with pytest.raises(ConfigurationError, match="cannot make Rastro start a program"):
        run_job_file(job)
    with pytest.raises(ConfigurationError, match="cannot make Rastro start a program"):
        run_job_file(job, allow_remote=True, allow_env=["PATH"])
    monkeypatch.setenv("RASTRO_ALLOW_REMOTE", "1")
    monkeypatch.setenv("RASTRO_ALLOW_COMMAND", "1")  # not a Rastro opt-in: ignored
    with pytest.raises(ConfigurationError, match="cannot make Rastro start a program"):
        run_job_file(job)
    remote = CommandProvider(argv=("llm",), allow_remote=True)
    with operator_trust(allow_remote=True), pytest.raises(ConfigurationError):
        _build_model_provider(remote)
    assert not marker.exists()
    with pytest.raises(ValueError, match="allow_remote"):
        CommandProvider(argv=("llm",))  # the job must still declare where data goes


def test_cli_rejects_the_removed_allow_command_flag(tmp_path, capsys):
    job = hostile_job(tmp_path, 'provider = "command"\nargv = ["llm"]\nlocal = true')
    assert main(["job", "run", str(job), "--allow-command"]) != 0
    assert main(["job", "run", str(job)]) != 0
    assert "start a program" in capsys.readouterr().err


def test_cli_flags_are_hoisted_and_scoped(tmp_path, sink, capsys):
    argv = ["mcp", "--allow-remote", "--allow-env", "A", "--allow-egress", "--", "--allow-env"]
    assert hoist_trust_flags(argv) == [
        "--allow-remote",
        "--allow-env",
        "A",
        "--allow-egress",
        "mcp",
        "--",
        "--allow-env",
    ]
    job = hostile_job(tmp_path, EXFILTRATE.replace("AWS_SECRET_ACCESS_KEY", "RASTRO_MODEL_KEY"))
    assert main(["job", "run", str(job)]) != 0
    error = json.loads(capsys.readouterr().err)
    assert "--allow-remote" in json.dumps(error)
    assert not sink
    main(["job", "run", str(job), "--allow-remote"])
    assert sink
    sink.clear()
    assert main(["job", "run", str(job)]) != 0  # the opt-in did not outlive the command
    assert not sink


def test_repair_applies_operator_trust_to_job_models_only(monkeypatch):
    from types import SimpleNamespace

    from rastro.application.job_repair import _assist

    spec = OpenAICompatibleProvider(
        model="m", base_url="https://api.example.com/v1", allow_remote=True
    )
    context: Any = SimpleNamespace(
        job=SimpleNamespace(model=spec, repair=None, inference_policy=None)
    )
    with pytest.raises(ConfigurationError, match="--allow-remote"):
        _assist(context, None, False)
    assert _assist(context, None, True) is not None
    # [repair] model = true reads RASTRO_MODEL, which the operator set: trusted.
    monkeypatch.setenv("RASTRO_MODEL", "ollama:qwen3:4b")
    context.job = SimpleNamespace(
        model=None, repair=SimpleNamespace(model=True), inference_policy=None
    )
    assert _assist(context, None, False) is not None


def test_hoisted_allow_remote_reaches_subcommands_with_their_own_flag():
    from rastro.application.operator_trust import set_process_trust
    from rastro.cli import _build_parser
    from rastro.cli_trust import apply_trust

    argv = hoist_trust_flags(["studio", "--allow-remote"])
    args = _build_parser().parse_args(argv)
    previous = apply_trust(args, argv)
    try:
        assert args.allow_remote is True
    finally:
        set_process_trust(previous)


def test_trust_flags_after_the_subcommand_reach_explicit_options():
    from rastro.cli import _build_parser
    from rastro.cli_trust import trust_options

    argv = hoist_trust_flags(["job", "run", "j.toml", "--allow-egress", "--allow-env", "PROXY_PW"])
    args = _build_parser().parse_args(argv)
    assert trust_options(args) == {"allow_egress": True, "allow_env": ["PROXY_PW"]}
    plain = _build_parser().parse_args(["job", "run", "j.toml"])
    assert trust_options(plain) == {"allow_egress": False, "allow_env": []}


def test_operator_scope_extends_the_egress_secret_allow_list():
    from rastro.application.egress_trust import allowed_env_names

    assert "PROXY_PW" not in allowed_env_names((), {})
    with operator_trust(allow_env=["PROXY_PW"]):
        assert "PROXY_PW" in allowed_env_names((), {})
    assert "PROXY_PW" not in allowed_env_names((), {})
