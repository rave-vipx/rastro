"""Observe repeated structures and per-record field selectors in a saved page."""

from dataclasses import dataclass
from typing import Protocol

from rastro.domain.models import Limits


@dataclass(frozen=True)
class DiscoveredField:
    """One element present exactly once in every record, with its selector and values."""

    tag: str
    classes: tuple[str, ...]
    attribute: str | None
    selector: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveredRecords:
    record_selector: str
    fields: tuple[DiscoveredField, ...]


class FieldDiscoverer(Protocol):
    def discover(
        self, html: str, records_min: int, limits: Limits
    ) -> tuple[DiscoveredRecords, ...]:
        """Candidate record selectors with fields, best first; never fetch or execute anything.

        Raises ``ResourceLimitError`` when the page exceeds the discovery budget; a page
        without repeated structure returns an empty tuple.
        """
        ...
