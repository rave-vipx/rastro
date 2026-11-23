"""Built-in notifications: a plain-language, content-free message per run that needs attention.

Notifiers are operator configuration (CLI flags and environment), never read from a job file
or a bundle. A message is built only from the :class:`~rastro.domain.run_report.RunReport`:
job file name, status, counts, hosts and reason codes. It never holds records, page text,
URL paths or queries. Every destination is resolved here and checked like an acquisition
target (public addresses only unless the operator grants private networks; link-local and
cloud metadata addresses never), and the caller connects to the checked address. Failures
carry a code only, so a webhook token or password cannot reach a log.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from rastro.application.destinations import public_address
from rastro.domain.acquisition import AcquisitionError
from rastro.domain.run_report import RunReport

type Channel = Literal["email", "slack", "telegram", "webhook"]
CHANNELS: tuple[Channel, ...] = ("email", "slack", "telegram", "webhook")
TIMEOUT_SECONDS = 10.0
_LISTED_SOURCES = 10


class NotificationError(Exception):
    """A delivery failure named by a code; it never carries a URL, token or reply body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Message:
    """What every channel sends: a subject, a short text and the report as JSON."""

    event: str
    subject: str
    text: str
    report: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    channel: Channel

    def send(self, message: Message) -> None: ...


def message_for(report: RunReport) -> Message:
    """Describe a run in plain words from its content-free report."""
    totals = report.totals
    lines = [
        f"{totals.succeeded} of {totals.sources} sources succeeded; "
        f"{totals.failed} failed, {totals.blocked} blocked, {totals.partial} partial.",
        f"Records accepted: {totals.records_accepted}, rejected: {totals.records_rejected}.",
    ]
    if report.open_drift:
        lines.append(f"Sources with open drift: {report.open_drift}.")
    troubled = [s for s in report.sources if s.outcome not in {"success", "unchanged"}]
    for source in troubled[:_LISTED_SOURCES]:
        detail = f" ({source.detail})" if source.detail else ""
        lines.append(f"{source.host}: {source.reason}{detail}")
    if len(troubled) > _LISTED_SOURCES:
        lines.append(f"... and {len(troubled) - _LISTED_SOURCES} more.")
    return Message(
        event="rastro_job_attention",
        subject=f"Rastro: {report.job} needs attention (exit {report.exit_code})",
        text="\n".join(lines),
        report=report.model_dump(mode="json"),
    )


def test_message() -> Message:
    """The message each channel's test sends; it names no job and holds no report."""
    return Message(
        event="rastro_test",
        subject="Rastro: test notification",
        text="Notifications from Rastro reach this channel.",
    )


def system_resolve(host: str, port: int) -> tuple[str, ...]:
    try:
        entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise NotificationError("dns_failure") from None
    return tuple(dict.fromkeys(str(entry[4][0]) for entry in entries))


def pin_address(host: str, port: int, *, allow_private: bool = False) -> str:
    """Resolve ``host`` and return one checked address; a mixed answer is refused whole."""
    addresses = system_resolve(host, port)
    if not addresses:
        raise NotificationError("dns_failure")
    try:
        checked = [public_address(a, allow_private=allow_private) for a in addresses]
    except AcquisitionError:
        raise NotificationError("destination_denied") from None
    return checked[0]


def deliver(notifiers: Iterable[Notifier], message: Message) -> tuple[tuple[str, str], ...]:
    """Send to every notifier; return ``(channel, code)`` per failure and never raise."""
    failures: list[tuple[str, str]] = []
    for notifier in notifiers:
        try:
            notifier.send(message)
        except NotificationError as error:
            failures.append((notifier.channel, error.code))
        except Exception:  # a notifier bug must not change the run's outcome
            failures.append((notifier.channel, "notifier_error"))
    return tuple(failures)
