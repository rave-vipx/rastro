"""Email notifier over stdlib ``smtplib``, always encrypted (STARTTLS or implicit TLS).

The SMTP host is resolved and checked like any other destination, and the socket goes to the
checked address while TLS verifies the certificate for the host name. The password stays
private to the notifier; failures carry a code only.
"""

from __future__ import annotations

import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Literal

from rastro.application.notifications import (
    TIMEOUT_SECONDS,
    Channel,
    Message,
    NotificationError,
    pin_address,
)
from rastro.domain.models import ConfigurationError

type Security = Literal["starttls", "ssl"]
_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_MAX_RECIPIENTS = 10


class _PinnedSession(smtplib.SMTP):
    def __init__(
        self, address: str, security: Security, context: ssl.SSLContext, timeout: float
    ) -> None:
        self._pinned = address
        self._implicit_tls = security == "ssl"
        self._tls_context = context
        super().__init__(timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        sock = socket.create_connection((self._pinned, port), timeout)
        if self._implicit_tls:
            return self._tls_context.wrap_socket(sock, server_hostname=host)
        return sock


class PinnedSMTP:
    """An encrypted SMTP session to ``address``; certificates are checked for ``host``."""

    def __init__(
        self, host: str, port: int, address: str, security: Security, timeout: float
    ) -> None:
        context = ssl.create_default_context()
        self._smtp = _PinnedSession(address, security, context, timeout)
        self._smtp.connect(host, port)
        if security == "starttls":
            self._smtp.ehlo()
            self._smtp.starttls(context=context)
            self._smtp.ehlo()

    def login(self, user: str, password: str) -> None:
        self._smtp.login(user, password)

    def send_message(self, message: EmailMessage) -> None:
        self._smtp.send_message(message)

    def quit(self) -> None:
        self._smtp.quit()


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise ConfigurationError(
            f"The email notification {what} is malformed", location="notify.email"
        )
    return value


class EmailNotifier:
    channel: Channel = "email"

    def __init__(
        self,
        *,
        host: str,
        sender: str,
        recipients: tuple[str, ...],
        security: str = "starttls",
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        allow_private: bool = False,
    ) -> None:
        if security not in {"starttls", "ssl"}:
            raise ConfigurationError(
                "Email notifications need security = starttls or ssl", location="notify.email"
            )
        if not 1 <= len(recipients) <= _MAX_RECIPIENTS:
            raise ConfigurationError(
                f"Email notifications need 1 to {_MAX_RECIPIENTS} recipients",
                location="notify.email",
            )
        if port is not None and not 1 <= port <= 65535:
            raise ConfigurationError("The SMTP port must be 1..65535", location="notify.email")
        if (username is None) != (password is None):
            raise ConfigurationError(
                "SMTP login needs both a username and a password", location="notify.email"
            )
        self._host = _check(_HOST, host, "SMTP host").lower()
        self._sender = _check(_ADDRESS, sender, "sender")
        self._recipients = tuple(_check(_ADDRESS, r, "recipient") for r in recipients)
        self._security: Security = "ssl" if security == "ssl" else "starttls"
        self._port = port or (465 if self._security == "ssl" else 587)
        self._login = (username, password) if username and password else None
        self._allow_private = allow_private

    def __repr__(self) -> str:
        return f"EmailNotifier(host={self._host!r}, recipients={len(self._recipients)})"

    def send(self, message: Message) -> None:
        email = EmailMessage()
        email["Subject"] = message.subject
        email["From"] = self._sender
        email["To"] = ", ".join(self._recipients)
        email.set_content(message.text)
        address = pin_address(self._host, self._port, allow_private=self._allow_private)
        try:
            smtp = PinnedSMTP(self._host, self._port, address, self._security, TIMEOUT_SECONDS)
            try:
                if self._login is not None:
                    smtp.login(*self._login)
                smtp.send_message(email)
            finally:
                smtp.quit()
        except ssl.SSLCertVerificationError:
            raise NotificationError("tls_verification_failed") from None
        except smtplib.SMTPNotSupportedError:
            raise NotificationError("tls_unavailable") from None
        except smtplib.SMTPAuthenticationError:
            raise NotificationError("authentication_failed") from None
        except TimeoutError:
            raise NotificationError("timeout") from None
        except (OSError, smtplib.SMTPException):
            raise NotificationError("network_failure") from None
