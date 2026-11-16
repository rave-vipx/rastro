"""A spider run's content-free report: every attempted page, host and path only (N3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rastro.domain.sources import Contract

MAX_REPORTED_PAGES = 600

SpiderOutcome = Literal[
    "fetched",  # the page was acquired and extracted
    "no_records",  # acquired, but it holds links only
    "partial",  # acquired; pagination inside it stopped early
    "failed",  # acquisition or extraction failed
    "robots_denied",  # robots.txt disallows it; the operator's grant is the only override
    "off_host",
    "https_downgrade",
    "path_denied",  # outside `allow` or inside `deny`
    "invalid_url",
    "depth_limit",
    "page_limit",
    "budget",
]


class SpiderPage(Contract):
    """One discovered URL and what became of it: host and path only, never a query."""

    ordinal: int = Field(ge=1)
    depth: int = Field(ge=0, le=4)
    host: str = Field(max_length=253)
    path: str = Field(max_length=256)
    outcome: SpiderOutcome
    records: int = Field(default=0, ge=0)


class SpiderReport(Contract):
    """Every attempted page in discovery order, capped; ``unreported`` counts the rest."""

    pages: tuple[SpiderPage, ...] = Field(max_length=MAX_REPORTED_PAGES)
    discovered: int = Field(ge=0)
    unreported: int = Field(default=0, ge=0)
