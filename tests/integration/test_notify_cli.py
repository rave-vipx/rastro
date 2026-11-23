"""Built-in notifiers on `rastro job load|run`: failures never change the exit code."""

import json

import pytest

from rastro.adapters import notify_http
from rastro.application import notifications
from rastro.cli import main
from rastro.cli_notify import notify_test_command
from tests.integration.test_open_drift_load import JOB, page

HOOK_URL = "https://hooks.example/services/s3cr3tt0ken"


@pytest.fixture
def job(tmp_path, monkeypatch):
    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "page.html").write_text("<html><body>private text</body></html>")
    monkeypatch.setenv("RASTRO_NOTIFY_WEBHOOK_URL", HOOK_URL)
    return tmp_path / "job.toml"


def issues(err: str) -> list[dict[str, str]]:
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


@pytest.mark.parametrize("command", ["load", "run"])
def test_notifier_failure_does_not_change_exit(job, capsys, monkeypatch, command):
    monkeypatch.setattr(notifications, "system_resolve", lambda host, port: ("10.1.2.3",))
    assert main(["job", command, str(job), "--notify", "webhook"]) == 1
    err = capsys.readouterr().err
    assert {
        "issue": "notification_failed",
        "channel": "webhook",
        "reason": "destination_denied",
    } in (issues(err))
    assert "s3cr3t" not in err and "hooks.example" not in err


def test_notifier_runs_only_on_attention(job, capsys, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify_http.WebhookNotifier, "send", lambda self, m: sent.append(m.subject))
    (job.parent / "page.html").write_text(page(2), encoding="utf-8")
    monkeypatch.setenv("RASTRO_NOTIFY", "webhook")
    assert main(["job", "load", str(job)]) == 0
    assert sent == []
    (job.parent / "page.html").write_text("<html><body>private text</body></html>")
    assert main(["job", "load", str(job)]) == 1
    assert sent == ["Rastro: job.toml needs attention (exit 1)"]


def test_missing_setting_is_a_usage_error_before_the_job_runs(job, capsys, monkeypatch):
    monkeypatch.delenv("RASTRO_NOTIFY_WEBHOOK_URL")
    assert main(["job", "load", str(job), "--notify", "webhook"]) == 2
    assert "RASTRO_NOTIFY_WEBHOOK_URL" in capsys.readouterr().err


def test_notify_test_command_sends_and_reports(job, capsys, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify_http.WebhookNotifier, "send", lambda self, m: sent.append(m.subject))
    assert notify_test_command(["webhook"]) == 0
    assert sent == ["Rastro: test notification"]
    assert json.loads(capsys.readouterr().out) == {"channel": "webhook", "sent": True}


def test_notify_test_command_reports_a_refused_destination(job, capsys, monkeypatch):
    monkeypatch.setattr(notifications, "system_resolve", lambda host, port: ("127.0.0.1",))
    assert notify_test_command(["webhook"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"channel": "webhook", "sent": False, "reason": "destination_denied"}
