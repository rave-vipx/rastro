"""Engine strategy ports: engine-neutral interfaces for features beyond fetch and parse.

Each port has one adapter today, backed by the default engine; another engine can implement
the same port. ``NetworkCapture`` and ``PageStepRunner`` are optional features of a browser
session and are detected with ``isinstance``, so sessions without them keep working.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.page_steps import PageStep
from rastro.domain.sources import Limits

__all__ = [
    "AdaptiveLocator",
    "CapturedResponse",
    "CrawlThrottle",
    "NetworkCapture",
    "PageCondenser",
    "PageStepRunner",
    "RecordCandidate",
]


@dataclass(frozen=True)
class RecordCandidate:
    """A repeated element group that looks like records, with a locator per field."""

    record_selector: str
    count: int
    field_locators: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("count must not be negative")
        object.__setattr__(self, "field_locators", MappingProxyType(dict(self.field_locators)))


class AdaptiveLocator(Protocol):
    """Find records and stable selectors by similarity instead of a fixed path."""

    def similar_records(
        self, html: str, anchor_text: str, *, limits: Limits
    ) -> tuple[RecordCandidate, ...]:
        """Record groups whose elements resemble the one containing ``anchor_text``."""
        ...

    def stable_selector(self, html: str, locator: str, *, limits: Limits) -> str | None:
        """A selector for the element at ``locator`` that survives small layout changes."""
        ...

    def anchor_by_label(
        self, html: str, record_selector: str, label: str, *, limits: Limits
    ) -> str | None:
        """A selector, relative to each record, for the value next to visible ``label``."""
        ...


class PageCondenser(Protocol):
    """Turn a page into short Markdown for a model, without hidden content."""

    def condense(self, html: str, *, max_chars: int) -> str: ...


class CrawlThrottle(Protocol):
    """Adaptive per-host delay: never below ``floor``, longer when a host slows or refuses."""

    def delay_for(self, host: str, floor: float) -> float:
        """Seconds to wait before the next request to ``host``."""
        ...

    def record(self, host: str, *, latency: float, ok: bool, retry_after: float | None) -> float:
        """Record one response and return the host's new delay in seconds."""
        ...


@dataclass(frozen=True)
class CapturedResponse:
    """One network response seen by the page. The path never carries a query."""

    host: str
    path: str
    method: Literal["GET", "POST"]
    status: int
    media_type: str
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or "?" in self.path or "#" in self.path:
            raise ValueError("path must be absolute and carry no query or fragment")
        if not 100 <= self.status <= 599:
            raise ValueError("status must be an HTTP status code")


@runtime_checkable
class NetworkCapture(Protocol):
    """Optional on a browser session: the responses captured for the current page."""

    def captured(self) -> tuple[CapturedResponse, ...]: ...


@runtime_checkable
class PageStepRunner(Protocol):
    """Optional on a browser session: run page steps and return the resulting document."""

    async def run_steps(
        self, steps: Sequence[PageStep], *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult: ...
