"""A job file cannot route traffic through its own proxy or read arbitrary secrets (audit H1).

The audit PoC declared ``[acquisition.egress_profiles]`` pointing at an attacker proxy with
``password_env = "GITHUB_TOKEN"``; Rastro sent the token as ``Proxy-Authorization``. Trust is
now the operator's: job-declared egress needs an explicit opt-in, and credential references
must be ``RASTRO_*`` or on the operator allow-list.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest

from rastro import load_job_file, run_job_file
from rastro.application.job_preview import preview_job_file
from rastro.application.repair_context import RepairContext
from rastro.cli import main
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError, Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

HTML = "<html><body><h1>Hello</h1></body></html>"


def _job(port: int, user: str = "USER", password: str = "GITHUB_TOKEN") -> str:
    return f"""format_version = 1
discover_recipes = true
[region]
egress = "p"
[acquisition]
allowed_hosts = ["example.com"]
max_retries = 0
[acquisition.egress_profiles.p]
proxy_url = "http://127.0.0.1:{port}"
username_env = "{user}"
password_env = "{password}"
[[sources]]
url = "https://example.com/"
[[fields]]
name = "name"
type = "string"
[dataset]
path = "data.sqlite3"
name = "d"
identity = ["name"]
"""


class _Acquirer:
    async def acquire(self, source: Any, *, max_bytes: int, representation: str) -> Any:
        return AcquisitionResult(url=source.url, body=HTML, status=Outcome.SUCCESS, issue_code=None)


@pytest.fixture
def sink() -> Any:
    """A listening socket standing in for the attacker proxy; nothing may connect to it."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(0.2)
    yield server
    server.close()


def _connected(server: socket.socket) -> bool:
    try:
        connection, _ = server.accept()
    except TimeoutError:
        return False
    connection.close()
    return True


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_synthetic_not_a_real_token")
    monkeypatch.setenv("USER", "someone")
    monkeypatch.setenv("RASTRO_PROXY_USER", "u")
    monkeypatch.setenv("RASTRO_PROXY_PASS", "p")
    monkeypatch.delenv("RASTRO_ALLOW_EGRESS", raising=False)
    monkeypatch.delenv("RASTRO_ALLOWED_ENV", raising=False)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "job.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.enable_socket
@pytest.mark.parametrize("entry", [run_job_file, load_job_file, preview_job_file])
def test_job_declared_proxy_needs_operator_opt_in(
    entry: Any, tmp_path: Path, sink: socket.socket
) -> None:
    job = _write(tmp_path, _job(sink.getsockname()[1]))
    with pytest.raises(ConfigurationError, match="allow-egress"):
        entry(job)
    assert not _connected(sink)


@pytest.mark.enable_socket
@pytest.mark.parametrize("entry", [run_job_file, load_job_file, preview_job_file])
def test_opt_in_still_refuses_unapproved_secret_names(
    entry: Any, tmp_path: Path, sink: socket.socket
) -> None:
    job = _write(tmp_path, _job(sink.getsockname()[1]))
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOWED_ENV") as caught:
        entry(job, allow_egress=True)
    assert "GITHUB" not in str(caught.value)
    assert caught.value.location == "acquisition.egress_profiles.p.username_env"
    assert not _connected(sink)


def test_opt_in_with_rastro_names_runs(tmp_path: Path) -> None:
    job = _write(tmp_path, _job(9, "RASTRO_PROXY_USER", "RASTRO_PROXY_PASS"))
    report = run_job_file(job, allow_egress=True, acquisition=_Acquirer())
    assert report.job.source_count == 1


def test_operator_allow_list_from_sdk(tmp_path: Path) -> None:
    job = _write(tmp_path, _job(9))
    report = run_job_file(
        job, allow_egress=True, allow_env=("USER", "GITHUB_TOKEN"), acquisition=_Acquirer()
    )
    assert report.job.source_count == 1


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_operator_opt_in_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    job = _write(tmp_path, _job(9))
    monkeypatch.setenv("RASTRO_ALLOW_EGRESS", value)
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOWED_ENV"):
        run_job_file(job, acquisition=_Acquirer())
    monkeypatch.setenv("RASTRO_ALLOWED_ENV", "USER, GITHUB_TOKEN")
    assert run_job_file(job, acquisition=_Acquirer()).job.source_count == 1


def test_jobs_without_egress_profiles_need_no_opt_in(tmp_path: Path) -> None:
    text = _job(9).replace('egress = "p"\n', "").split("[acquisition.egress_profiles.p]")[0]
    text += (
        '[[sources]]\nurl = "https://example.com/"\n[[fields]]\nname = "name"\ntype = "string"\n'
    )
    report = run_job_file(_write(tmp_path, text), acquisition=_Acquirer())
    assert report.job.source_count == 1


def test_repair_refetch_needs_operator_opt_in(tmp_path: Path) -> None:
    job = parse_job(_job(9).encode(), format="toml")
    context = RepairContext(job, job.build_schema(), None, tmp_path / "d", tmp_path, False)  # type: ignore[arg-type]
    source = job.region.to_source("https://example.com/")
    with (
        pytest.raises(ConfigurationError, match="allow-egress"),
        asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner,
    ):
        runner.run(context.document(1, source))


@pytest.mark.parametrize("command", ["run", "preview", "load"])
def test_cli_passes_operator_flags(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _write(tmp_path, _job(9))
    assert main(["job", command, str(job)]) == 2
    seen: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise ConfigurationError("stop")

    target = {
        "run": "rastro.cli.run_job_file",
        "preview": "rastro.cli.preview_job_file",
        "load": "rastro.cli_dataset.load_job_file",
    }[command]
    monkeypatch.setattr(target, capture)
    main(["job", command, str(job), "--allow-egress", "--allow-env", "USER", "--allow-env", "X"])
    assert seen["allow_egress"] is True
    assert seen["allow_env"] == ["USER", "X"]
