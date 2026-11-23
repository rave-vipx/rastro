"""Slack, Telegram and webhook notifiers over stdlib HTTPS to a checked, pinned address.

The URL (which may hold a token) is kept private to the notifier and never appears in its
``repr``, errors or logs. The connection goes to the address :func:`pin_address` checked,
while TLS still verifies the certificate for the URL's host name; redirects are not followed.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
from typing import Any, Protocol
from urllib.parse import urlsplit

from rastro.application.notifications import (
    TIMEOUT_SECONDS,
    Channel,
    Message,
    NotificationError,
    pin_address,
)
from rastro.domain.models import ConfigurationError

_MAX_URL = 2048
_REPLY_BYTES = 4096
_TELEGRAM_HOST = "api.telegram.org"
_BOT_TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,100}")
_CHAT_ID = re.compile(r"-?[0-9]{1,20}|@[A-Za-z0-9_]{5,64}")


class _Connection(Protocol):
    def request(self, method: str, target: str, body: bytes, headers: dict[str, str]) -> None: ...
    def getresponse(self) -> Any: ...
    def close(self) -> None: ...


class PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to ``address`` with SNI and certificate checks for ``host``."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def https_target(url: str, *, location: str) -> tuple[str, int, str]:
    """Split an operator URL into host, port and request target; refuse anything but plain HTTPS."""
    problem = len(url) > _MAX_URL or any(ord(c) < 0x21 or ord(c) == 0x7F for c in url)
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError:
        problem = True
    if problem or parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        raise ConfigurationError(
            "The notification URL must be an https:// URL without credentials", location=location
        )
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.hostname.lower().rstrip("."), port, target


def post_json(
    host: str, port: int, target: str, body: dict[str, Any], *, allow_private: bool
) -> None:
    address = pin_address(host, port, allow_private=allow_private)
    connection: _Connection = PinnedConnection(host, port, address, TIMEOUT_SECONDS)
    payload = json.dumps(body, ensure_ascii=True).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "rastro-notify"}
    try:
        connection.request("POST", target, payload, headers)
        response = connection.getresponse()
        response.read(_REPLY_BYTES)
        status = int(response.status)
    except ssl.SSLCertVerificationError:
        raise NotificationError("tls_verification_failed") from None
    except TimeoutError:
        raise NotificationError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise NotificationError("network_failure") from None
    finally:
        connection.close()
    if not 200 <= status < 300:
        raise NotificationError("rejected_status")


class _UrlNotifier:
    channel: Channel
    _location: str

    def __init__(self, url: str, *, allow_private: bool = False) -> None:
        self._host, self._port, self._target = https_target(url, location=self._location)
        self._allow_private = allow_private

    def __repr__(self) -> str:
        return f"{type(self).__name__}(host={self._host!r})"

    def _body(self, message: Message) -> dict[str, Any]:
        raise NotImplementedError

    def send(self, message: Message) -> None:
        post_json(
            self._host,
            self._port,
            self._target,
            self._body(message),
            allow_private=self._allow_private,
        )


class WebhookNotifier(_UrlNotifier):
    """POST ``{event, subject, text, report}`` as JSON to an operator URL."""

    channel: Channel = "webhook"
    _location = "notify.webhook"

    def _body(self, message: Message) -> dict[str, Any]:
        return {
            "event": message.event,
            "subject": message.subject,
            "text": message.text,
            "report": message.report,
        }


class SlackNotifier(_UrlNotifier):
    """Slack incoming webhook (or any service taking ``{"text": ...}``)."""

    channel: Channel = "slack"
    _location = "notify.slack"

    def _body(self, message: Message) -> dict[str, Any]:
        return {"text": f"{message.subject}\n{message.text}"}


class TelegramNotifier:
    """Telegram Bot API ``sendMessage`` to one chat."""

    channel: Channel = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        if not _BOT_TOKEN.fullmatch(token):
            raise ConfigurationError(
                "The Telegram bot token is malformed", location="notify.telegram"
            )
        if not _CHAT_ID.fullmatch(chat_id):
            raise ConfigurationError(
                "The Telegram chat id must be a number or an @channel name",
                location="notify.telegram",
            )
        self._token = token
        self._chat_id = chat_id

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    def send(self, message: Message) -> None:
        body = {"chat_id": self._chat_id, "text": f"{message.subject}\n{message.text}"}
        target = f"/bot{self._token}/sendMessage"
        post_json(_TELEGRAM_HOST, 443, target, body, allow_private=False)
