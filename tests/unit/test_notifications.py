"""Built-in notifiers: operator-only settings, public destinations, content-free messages."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from rastro.adapters import notify_http, notify_smtp
from rastro.application import notifications
from rastro.application.notifications import NotificationError, message_for
from rastro.cli_notify import notifiers_from_env
from rastro.domain.models import ConfigurationError
from rastro.domain.run_report import RunReport, RunTotals, SourceReport

START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
HOOK_URL = "https://hooks.example/services/T000/B000/s3cr3tt0ken"
BOT_TOKEN = "123456:abcdefghijklmnopqrstuvwxyz"


def failed_report() -> RunReport:
    sources = (
        SourceReport(
            source_index=1,
            host="shop.example",
            path="/private/catalog",
            outcome="access_restriction",
            reason="blocked",
            detail="http_403",
        ),
        SourceReport(
            source_index=2,
            host="news.example",
            path="/list",
            outcome="success",
            reason="complete",
            pages=1,
            records_accepted=3,
        ),
    )
    totals = dict.fromkeys(RunTotals.model_fields, 0) | {
        "sources": 2,
        "succeeded": 1,
        "blocked": 1,
        "records_accepted": 3,
    }
    return RunReport(
        kind="load",
        job="prices.toml",
        started_at=START,
        completed_at=START + timedelta(seconds=4),
        status="partial_success",
        exit_code=3,
        sources=sources,
        totals=RunTotals(**totals),
    )


class FakeConnection:
    sent: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        self.call = {"host": host, "port": port, "address": address}
        FakeConnection.sent.append(self.call)

    def request(self, method: str, target: str, body: bytes, headers: dict[str, str]) -> None:
        self.call.update(method=method, target=target, body=json.loads(body), headers=headers)

    def getresponse(self) -> Any:
        class Response:
            status = 200

            def read(self, size: int) -> bytes:
                return b"ok"

        return Response()

    def close(self) -> None:
        pass


@pytest.fixture
def connection(monkeypatch):
    FakeConnection.sent = []
    monkeypatch.setattr(notify_http, "PinnedConnection", FakeConnection)
    return FakeConnection.sent


def resolve_to(*addresses: str):
    def resolve(host: str, port: int) -> tuple[str, ...]:
        return addresses

    return resolve


def test_webhook_private_ip_refused(monkeypatch, connection):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34", "10.0.0.5"))
    notifier = notify_http.WebhookNotifier(HOOK_URL)
    with pytest.raises(NotificationError) as caught:
        notifier.send(message_for(failed_report()))
    assert caught.value.code == "destination_denied"
    assert connection == []
    assert "s3cr3t" not in repr(notifier) and "s3cr3t" not in str(caught.value)


def test_metadata_address_refused_even_with_the_private_grant(monkeypatch, connection):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("169.254.169.254"))
    notifier = notify_http.WebhookNotifier(HOOK_URL, allow_private=True)
    with pytest.raises(NotificationError, match="destination_denied"):
        notifier.send(message_for(failed_report()))
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("10.0.0.5"))
    notifier.send(message_for(failed_report()))
    assert connection[0]["address"] == "10.0.0.5"


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example/x",
        "https://user:pw@hooks.example/x",
        "https:///x",
        "ftp://hooks.example/x",
        "https://hooks.example/x\r\nHost: evil.example",
    ],
)
def test_webhook_url_must_be_plain_https(url):
    with pytest.raises(ConfigurationError) as caught:
        notify_http.WebhookNotifier(url)
    assert "pw" not in str(caught.value)


def test_webhook_posts_the_pinned_address_with_a_content_free_report(monkeypatch, connection):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))
    notify_http.WebhookNotifier(HOOK_URL).send(message_for(failed_report()))
    (call,) = connection
    assert (call["host"], call["port"], call["address"]) == ("hooks.example", 443, "93.184.216.34")
    assert call["target"] == "/services/T000/B000/s3cr3tt0ken"
    assert call["body"]["event"] == "rastro_job_attention"
    assert call["body"]["report"]["content"] == "content_free"
    posted = json.dumps(call["body"])
    assert "/private/catalog" not in posted and '"path"' not in posted


def test_payload_is_content_free_report():
    message = message_for(failed_report())
    assert message.subject == "Rastro: prices.toml needs attention (exit 3)"
    assert "1 of 2 sources succeeded" in message.text
    assert "shop.example: blocked (http_403)" in message.text
    assert message.report["job"] == "prices.toml"
    for leaked in ("/private/catalog", "?", "token"):
        assert leaked not in message.text
    assert all("path" not in source for source in message.report["sources"])
    assert "/private/catalog" not in json.dumps(message.report)


def test_slack_and_telegram_bodies(monkeypatch, connection):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))
    message = message_for(failed_report())
    notify_http.SlackNotifier(HOOK_URL).send(message)
    notify_http.TelegramNotifier(BOT_TOKEN, "-1001234").send(message)
    slack, telegram = connection
    assert slack["body"] == {"text": f"{message.subject}\n{message.text}"}
    assert telegram["host"] == "api.telegram.org"
    assert telegram["target"] == f"/bot{BOT_TOKEN}/sendMessage"
    assert telegram["body"]["chat_id"] == "-1001234"
    assert "report" not in telegram["body"]


def test_refused_status_is_a_failure_without_details(monkeypatch, connection):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))

    class Refusing(FakeConnection):
        def getresponse(self) -> Any:
            class Response:
                status = 404

                def read(self, size: int) -> bytes:
                    return b"no such hook s3cr3t"

            return Response()

    monkeypatch.setattr(notify_http, "PinnedConnection", Refusing)
    with pytest.raises(NotificationError) as caught:
        notify_http.SlackNotifier(HOOK_URL).send(message_for(failed_report()))
    assert caught.value.code == "rejected_status" and "s3cr3t" not in str(caught.value)


def test_secret_from_disallowed_env_refused():
    environ = {
        "RASTRO_NOTIFY": "webhook",
        "RASTRO_NOTIFY_WEBHOOK_URL_ENV": "GITHUB_TOKEN",
        "GITHUB_TOKEN": HOOK_URL,
    }
    with pytest.raises(ConfigurationError) as caught:
        notifiers_from_env(None, environ)
    assert "GITHUB_TOKEN" not in str(caught.value) and "s3cr3t" not in str(caught.value)
    (notifier,) = notifiers_from_env(None, environ | {"RASTRO_ALLOWED_ENV": "GITHUB_TOKEN"})
    assert notifier.channel == "webhook"


def test_channels_come_from_flags_over_env_and_need_their_settings():
    environ = {"RASTRO_NOTIFY": "slack", "RASTRO_NOTIFY_WEBHOOK_URL": HOOK_URL}
    (notifier,) = notifiers_from_env(["webhook"], environ)
    assert notifier.channel == "webhook"
    assert notifiers_from_env([], environ) == ()
    with pytest.raises(ConfigurationError, match="RASTRO_NOTIFY_SLACK_WEBHOOK_URL"):
        notifiers_from_env(None, environ)
    with pytest.raises(ConfigurationError, match="unknown notification channel"):
        notifiers_from_env(["pager"], environ)


def test_deliver_reports_each_failure_and_never_raises():
    class Broken:
        channel = "slack"

        def send(self, message: notifications.Message) -> None:
            raise NotificationError("network_failure")

    class Crashing:
        channel = "webhook"

        def send(self, message: notifications.Message) -> None:
            raise RuntimeError(HOOK_URL)

    failures = notifications.deliver((Broken(), Crashing()), message_for(failed_report()))
    assert failures == (("slack", "network_failure"), ("webhook", "notifier_error"))


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, address: str, security: str, timeout: float):
        self.args = (host, port, address, security)
        self.logins: list[tuple[str, str]] = []
        self.messages: list[Any] = []
        FakeSMTP.instances.append(self)

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))

    def send_message(self, message: Any) -> None:
        self.messages.append(message)

    def quit(self) -> None:
        pass


def email_env(**changes: str) -> dict[str, str]:
    return {
        "RASTRO_NOTIFY": "email",
        "RASTRO_NOTIFY_SMTP_HOST": "smtp.example",
        "RASTRO_NOTIFY_SMTP_USERNAME": "robot",
        "RASTRO_NOTIFY_SMTP_PASSWORD": "pa55word",
        "RASTRO_NOTIFY_EMAIL_FROM": "robot@mail.example",
        "RASTRO_NOTIFY_EMAIL_TO": "ops@mail.example, oncall@mail.example",
    } | changes


def test_email_sends_over_tls_to_the_pinned_address(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(notify_smtp, "PinnedSMTP", FakeSMTP)
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))
    (notifier,) = notifiers_from_env(None, email_env())
    assert "pa55word" not in repr(notifier)
    notifier.send(message_for(failed_report()))
    (smtp,) = FakeSMTP.instances
    assert smtp.args == ("smtp.example", 587, "93.184.216.34", "starttls")
    assert smtp.logins == [("robot", "pa55word")]
    (sent,) = smtp.messages
    assert sent["Subject"] == "Rastro: prices.toml needs attention (exit 3)"
    assert sent["To"] == "ops@mail.example, oncall@mail.example"


def test_email_refuses_private_servers_and_header_injection(monkeypatch):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("192.168.1.2"))
    (notifier,) = notifiers_from_env(None, email_env())
    with pytest.raises(NotificationError, match="destination_denied"):
        notifier.send(message_for(failed_report()))
    with pytest.raises(ConfigurationError):
        notifiers_from_env(None, email_env(RASTRO_NOTIFY_EMAIL_TO="a@mail.example\r\nBcc: x@y.z"))
    with pytest.raises(ConfigurationError):
        notifiers_from_env(None, email_env(RASTRO_NOTIFY_SMTP_SECURITY="none"))


def test_test_message_names_no_job():
    message = notifications.test_message()
    assert message.subject == "Rastro: test notification"
    assert message.report == {}


class FakeTLS:
    def __init__(self) -> None:
        self.server_hostname: str | None = None

    def wrap_socket(self, sock: Any, server_hostname: str) -> Any:
        self.server_hostname = server_hostname
        return sock


def test_https_connection_dials_the_checked_address_and_verifies_the_host(monkeypatch):
    dialed: list[tuple[str, int]] = []
    monkeypatch.setattr(notify_http.socket, "create_connection", lambda a, t: dialed.append(a))
    connection = notify_http.PinnedConnection("hooks.example", 443, "93.184.216.34", 5.0)
    connection._context = tls = FakeTLS()
    connection.connect()
    assert dialed == [("93.184.216.34", 443)]
    assert tls.server_hostname == "hooks.example"


def test_smtp_session_dials_the_checked_address(monkeypatch):
    dialed: list[tuple[str, int]] = []
    monkeypatch.setattr(notify_smtp.socket, "create_connection", lambda a, t: dialed.append(a))
    tls = FakeTLS()
    session = notify_smtp._PinnedSession("93.184.216.34", "ssl", tls, 5.0)  # type: ignore[arg-type]
    session._get_socket("smtp.example", 465, 5.0)
    assert dialed == [("93.184.216.34", 465)] and tls.server_hostname == "smtp.example"
    plain = notify_smtp._PinnedSession("93.184.216.34", "starttls", tls, 5.0)  # type: ignore[arg-type]
    tls.server_hostname = None
    plain._get_socket("smtp.example", 587, 5.0)
    assert tls.server_hostname is None


@pytest.mark.parametrize(
    ("error", "code"), [(OSError("down"), "network_failure"), (TimeoutError(), "timeout")]
)
def test_transport_errors_become_codes(monkeypatch, connection, error, code):
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))

    class Failing(FakeConnection):
        def request(self, *args: Any) -> None:
            raise error

    monkeypatch.setattr(notify_http, "PinnedConnection", Failing)
    with pytest.raises(NotificationError) as caught:
        notify_http.WebhookNotifier(HOOK_URL).send(notifications.test_message())
    assert caught.value.code == code


def test_smtp_login_failure_is_a_code(monkeypatch):
    import smtplib

    class Refusing(FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad pa55word")

    monkeypatch.setattr(notify_smtp, "PinnedSMTP", Refusing)
    monkeypatch.setattr(notifications, "system_resolve", resolve_to("93.184.216.34"))
    (notifier,) = notifiers_from_env(None, email_env())
    with pytest.raises(NotificationError) as caught:
        notifier.send(notifications.test_message())
    assert caught.value.code == "authentication_failed" and "pa55word" not in str(caught.value)


def test_telegram_settings_are_checked_without_echo():
    with pytest.raises(ConfigurationError) as caught:
        notify_http.TelegramNotifier("not-a-token/../x", "-100")
    assert "not-a-token" not in str(caught.value)
    with pytest.raises(ConfigurationError):
        notify_http.TelegramNotifier(BOT_TOKEN, "chat; drop")
