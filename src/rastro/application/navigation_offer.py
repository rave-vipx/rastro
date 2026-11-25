"""Whether a first page looks like it hides records behind clicks, so exploration may help.

The check is a cheap, bounded look at the page a new job was built from: fewer records than
expected, a visible "load more" control, or a pagination control. It never fetches or runs
anything; it only suggests that ``explore_job`` could find the remaining records.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Literal

from rastro.domain.models import Contract

__all__ = ["ExplorationOffer", "OfferReason", "exploration_offer"]

type OfferReason = Literal["few_records", "load_more_control", "pagination_control"]
MAX_SCAN_CHARS = 512 * 1024

_HIDDEN = re.compile(
    r"<!--.*?-->|<(script|style|template|noscript)\b[^>]*>.*?</\1\s*>", re.I | re.S
)
_CONTROL = re.compile(r"<(button|a)\b([^>]{0,512})>(.{0,200}?)</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]{0,512}>")
_LOAD_MORE = re.compile(
    r"\b(?:load|show|see|view)\s+more\b|\bmore\s+results\b"
    r"|\b(?:ver|cargar|mostrar)\s+m[aá]s\b",
    re.I,
)
_PAGINATION = re.compile(
    r"""<a\b[^>]{0,512}\brel\s*=\s*["']?next\b"""
    r"""|\bclass\s*=\s*["'][^"']{0,256}\bpagination\b""",
    re.I,
)


class ExplorationOffer(Contract):
    """Why exploring the page may find more records, and which page to explore."""

    reason: OfferReason
    url: str | None = None


def exploration_offer(
    html: str, records: int, expected: int, *, url: str | None = None
) -> ExplorationOffer | None:
    """An offer when ``html`` shows fewer than ``expected`` records or a control hiding more."""
    visible = _HIDDEN.sub(" ", html[:MAX_SCAN_CHARS])
    for _, _, inner in _CONTROL.findall(visible):
        text = " ".join(unescape(_TAG.sub(" ", inner)).split())
        if _LOAD_MORE.search(text):
            return ExplorationOffer(reason="load_more_control", url=url)
    if _PAGINATION.search(visible):
        return ExplorationOffer(reason="pagination_control", url=url)
    if records < expected:
        return ExplorationOffer(reason="few_records", url=url)
    return None
