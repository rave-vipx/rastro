"""Describe a page's structure for prompts without fetching or executing anything."""

from typing import Protocol

from rastro.domain.models import Limits
from rastro.domain.page_outline import PageOutline


class PageOutliner(Protocol):
    def outline(self, html: str, limits: Limits) -> PageOutline:
        """Return a bounded outline; raise ResourceLimitError for oversized input."""
        ...
