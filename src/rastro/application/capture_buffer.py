"""The bounded buffer behind network capture: what one page render may keep.

Only XHR or fetch responses reach it (the browser adapter filters by resource type). It
accepts a JSON media type, an allowed host, a path (never the query) matching one of the
job's ``capture_network`` patterns, and keeps bodies within a count and byte budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.capture_pattern import capture_matches
from rastro.ports.engine import CapturedResponse

__all__ = [
    "MAX_CAPTURED_BYTES",
    "MAX_CAPTURED_RESPONSES",
    "MAX_RESPONSE_BYTES",
    "CaptureBuffer",
    "CaptureTicket",
    "is_json_media",
]

MAX_CAPTURED_RESPONSES = 16
"""The most responses one page render may capture."""
MAX_RESPONSE_BYTES = 2_000_000
"""The largest body kept; a larger response is dropped, never truncated."""
MAX_CAPTURED_BYTES = 8_000_000
"""The most body bytes one page render may hold."""
_METHODS = frozenset({"GET", "POST"})


def is_json_media(content_type: str) -> bool:
    """``application/json``, ``text/json`` or any ``+json`` media type, parameters ignored."""
    media = content_type.split(";", 1)[0].strip().lower()
    return media in {"application/json", "text/json"} or media.endswith("+json")


@dataclass(frozen=True)
class CaptureTicket:
    """A response accepted before its body is read: everything but the body."""

    host: str
    path: str
    method: str
    status: int
    media_type: str


class CaptureBuffer:
    """What one page render captured, under the job's patterns, hosts and budgets."""

    def __init__(self, policy: AcquisitionPolicy) -> None:
        self._policy = policy
        self._patterns = policy.browser.capture_network
        self._responses: list[CapturedResponse] = []
        self._accepted = self._bytes = 0
        self._closed = False

    def wants(
        self, url: str, method: str, status: int, content_type: str, content_length: str | None
    ) -> CaptureTicket | None:
        """A ticket for a response worth reading, or ``None``; spends one place when accepted."""
        method = method.upper()
        if (
            self._closed
            or self._accepted >= MAX_CAPTURED_RESPONSES
            or method not in _METHODS
            or not 100 <= status <= 599
            or not is_json_media(content_type)
            or _declared_size(content_length) > MAX_RESPONSE_BYTES
        ):
            return None
        try:
            permitted_url(url, self._policy)  # allowed host, scheme and port only
        except AcquisitionError:
            return None
        parts = urlsplit(url)
        path = parts.path or "/"
        if not any(capture_matches(p, path) for p in self._patterns):
            return None
        self._accepted += 1
        media = content_type.split(";", 1)[0].strip().lower()
        return CaptureTicket((parts.hostname or "").lower(), path, method, status, media)

    def add(self, ticket: CaptureTicket, body: bytes) -> bool:
        """Keep the body when it fits the budgets; the query was never part of the ticket."""
        size = len(body)
        if self._closed or size > MAX_RESPONSE_BYTES or self._bytes + size > MAX_CAPTURED_BYTES:
            return False
        self._bytes += size
        self._responses.append(
            CapturedResponse(
                ticket.host,
                ticket.path,
                "POST" if ticket.method == "POST" else "GET",
                ticket.status,
                ticket.media_type,
                body,
            )
        )
        return True

    def close(self) -> None:
        """Stop accepting: a body read that finishes later is dropped."""
        self._closed = True

    @property
    def responses(self) -> tuple[CapturedResponse, ...]:
        return tuple(self._responses)


def _declared_size(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0  # the body length is checked again once read
