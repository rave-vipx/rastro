"""Operator-only failure hook: no shell, timeout, content-free summary (OPS, SECURITY.md)."""

import json
import shlex
import sys

import pytest

from rastro import cli_hook
from rastro.cli import main
from tests.integration.test_open_drift_load import JOB, page

SECRET_URL = "https://shop.example/private/list?token=s3cr3t"
RECORD = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB.replace("https://shop.example/list", SECRET_URL))
    (tmp_path / "page.html").write_text(page(2), encoding="utf-8")
    return tmp_path / "job.toml"


def hook(*code):
    return shlex.join([sys.executable, "-c", *code])


def fail(job):
    (job.parent / "page.html").write_text("<html><body>private text</body></html>")


@pytest.mark.parametrize("command", ["load", "run"])
def test_hook_receives_a_content_free_summary_and_keeps_the_exit_code(job, capsys, command):
    out = job.parent / "summary.json"
    fail(job)
    assert main(["job", command, str(job), "--on-failure", hook(RECORD, str(out))]) == 1
    assert "on_failure_hook_failed" not in capsys.readouterr().err
    text = out.read_text()
    summary = json.loads(text)
    assert summary["job"] == "job.toml" and summary["command"] == command
    assert (summary["status"], summary["exit_code"]) == ("failed", 1)
    (source,) = summary["sources"]
    assert (source["source_index"], source["host"], source["outcome"]) == (
        1,
        "shop.example",
        "missing_data",
    )
    for leaked in ("token", "s3cr3t", "private", "page.html", str(job.parent)):
        assert leaked not in text


def test_hook_is_not_run_on_success_and_env_var_is_the_default(job, capsys, monkeypatch):
    out = job.parent / "summary.json"
    monkeypatch.setenv(cli_hook.ENV, hook(RECORD, str(out)))
    assert main(["job", "load", str(job)]) == 0
    assert not out.exists()
    fail(job)
    assert main(["job", "load", str(job)]) == 1
    assert json.loads(out.read_text())["exit_code"] == 1
    out.unlink()
    assert main(["job", "load", str(job), "--on-failure", ""]) == 1
    assert not out.exists()


def test_open_drift_triggers_the_hook_with_the_drift_level(job, capsys):
    out = job.parent / "summary.json"
    (job.parent / "page.html").write_text(page(5), encoding="utf-8")
    assert main(["job", "load", str(job), "--drift"]) == 0
    (job.parent / "page.html").write_text(page(1), encoding="utf-8")
    argv = ["job", "load", str(job), "--drift", "--on-failure", hook(RECORD, str(out))]
    assert main(argv) == 4
    summary = json.loads(out.read_text())
    assert (summary["status"], summary["exit_code"]) == ("degraded", 4)
    assert summary["sources"][0]["drift_risk"] == "elevated"
    assert summary["sources"][0]["open_drift"] is True


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import sys; sys.exit(3)", "exited with status 3"),
        ("import time; time.sleep(5)", "timed out"),
    ],
)
def test_hook_failure_is_reported_without_changing_the_exit_code(
    job, capsys, monkeypatch, code, message
):
    monkeypatch.setattr(cli_hook, "TIMEOUT_SECONDS", 0.5)
    fail(job)
    capsys.readouterr()
    assert main(["job", "load", str(job), "--on-failure", hook(code)]) == 1
    captured = capsys.readouterr()
    issue = json.loads(captured.err.strip().splitlines()[-1])
    assert issue["issue"] == "on_failure_hook_failed" and message in issue["message"]
    assert json.loads(captured.out)["status"] == "failed"


def test_missing_hook_program_and_bad_quoting(job, capsys):
    fail(job)
    assert main(["job", "load", str(job), "--on-failure", "/nonexistent/hook --token x"]) == 1
    err = capsys.readouterr().err
    assert "could not start" in err and "--token" not in err
    assert main(["job", "load", str(job), "--on-failure", "'unbalanced"]) == 2
    assert json.loads(capsys.readouterr().err)["location"] == "on_failure"


def test_job_files_cannot_configure_the_hook(job, capsys):
    job.write_text('on_failure = "curl https://evil.example"\n' + job.read_text())
    assert main(["job", "validate", str(job)]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "validation_error"
