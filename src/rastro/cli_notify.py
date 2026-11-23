"""Built-in notifiers from operator settings, and ``rastro notify test CHANNEL``.

Channels come from ``--notify CHANNEL`` (repeatable) or ``RASTRO_NOTIFY="slack,email"``; their
settings come only from the environment, never from a job file or bundle. A secret (webhook
URL, bot token, SMTP password) is read from its ``RASTRO_NOTIFY_*`` variable, or from another
variable named in ``<that name>_ENV`` when the operator allows that name (``RASTRO_ALLOWED_ENV``
or ``--allow-env``). Private destinations need the operator's private-network grant.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Mapping, Sequence

from rastro.application.egress_trust import allowed_env_names
from rastro.application.notifications import CHANNELS, Notifier, deliver, test_message
from rastro.application.operator_trust import PRIVATE_NETWORK_ENV, current_trust, env_flag
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name

ENV = "RASTRO_NOTIFY"


class _Settings:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._env = environ
        self._allowed = allowed_env_names(environ=environ)
        self.allow_private = (
            env_flag(PRIVATE_NETWORK_ENV, environ) or current_trust().allow_private_network
        )

    def value(self, name: str, *, required: bool = True) -> str | None:
        value = self._env.get(name, "").strip()
        if not value and required:
            raise ConfigurationError(f"Set {name} to use this notification channel")
        return value or None

    def secret(self, name: str, *, required: bool = True) -> str | None:
        source = self._env.get(f"{name}_ENV", "").strip() or name
        check_secret_env_name(source, self._allowed, location=f"{name}_ENV")
        value = self._env.get(source, "").strip()
        if not value and required:
            raise ConfigurationError(f"Set {name} (or name its variable in {name}_ENV)")
        return value or None


def _build(channel: str, settings: _Settings) -> Notifier:
    from rastro.adapters import notify_http, notify_smtp

    private = settings.allow_private
    if channel == "webhook":
        url = settings.secret("RASTRO_NOTIFY_WEBHOOK_URL") or ""
        return notify_http.WebhookNotifier(url, allow_private=private)
    if channel == "slack":
        url = settings.secret("RASTRO_NOTIFY_SLACK_WEBHOOK_URL") or ""
        return notify_http.SlackNotifier(url, allow_private=private)
    if channel == "telegram":
        token = settings.secret("RASTRO_NOTIFY_TELEGRAM_TOKEN") or ""
        chat = settings.value("RASTRO_NOTIFY_TELEGRAM_CHAT_ID") or ""
        return notify_http.TelegramNotifier(token, chat)
    port = settings.value("RASTRO_NOTIFY_SMTP_PORT", required=False)
    if port is not None and not port.isdigit():
        raise ConfigurationError("RASTRO_NOTIFY_SMTP_PORT must be a number")
    recipients = settings.value("RASTRO_NOTIFY_EMAIL_TO") or ""
    return notify_smtp.EmailNotifier(
        host=settings.value("RASTRO_NOTIFY_SMTP_HOST") or "",
        port=int(port) if port else None,
        security=settings.value("RASTRO_NOTIFY_SMTP_SECURITY", required=False) or "starttls",
        username=settings.value("RASTRO_NOTIFY_SMTP_USERNAME", required=False),
        password=settings.secret("RASTRO_NOTIFY_SMTP_PASSWORD", required=False),
        sender=settings.value("RASTRO_NOTIFY_EMAIL_FROM") or "",
        recipients=tuple(r.strip() for r in recipients.split(",") if r.strip()),
        allow_private=private,
    )


def notifiers_from_env(
    channels: Iterable[str] | None, environ: Mapping[str, str] | None = None
) -> tuple[Notifier, ...]:
    """Build the notifiers named by ``channels`` (or ``RASTRO_NOTIFY`` when ``None``)."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV, "").split(",") if channels is None else channels
    names = list(dict.fromkeys(name.strip().lower() for name in raw if name.strip()))
    unknown = [name for name in names if name not in CHANNELS]
    if unknown:
        raise ConfigurationError(
            f"unknown notification channel; choose from {', '.join(CHANNELS)}", location="notify"
        )
    settings = _Settings(env)
    return tuple(_build(name, settings) for name in names)


def add_notify_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--notify",
        action="append",
        metavar="CHANNEL",
        help=f"Notify {', '.join(CHANNELS)} when the job exits non-zero (repeatable); "
        f"overrides {ENV}",
    )


def add_notify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    notify = subparsers.add_parser("notify", help="Check notification channels")
    actions = notify.add_subparsers(dest="notify_command", required=True)
    test = actions.add_parser("test", help="Send a test notification to each channel")
    test.add_argument("channels", nargs="+", metavar="CHANNEL", choices=CHANNELS)


def notify_test_command(channels: Sequence[str]) -> int:
    """Send the test message to each channel; print one JSON line each; exit 1 on a failure."""
    notifiers = notifiers_from_env(channels)
    failed = False
    for notifier in notifiers:
        failures = deliver((notifier,), test_message())
        result: dict[str, object] = {"channel": notifier.channel, "sent": not failures}
        if failures:
            failed = True
            result["reason"] = failures[0][1]
        print(json.dumps(result))
    return 1 if failed else 0
