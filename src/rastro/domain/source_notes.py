"""Source notes: what happened to one source, as names and counts only.

Each part of a run (acquisition, discovery, crawl, repair) fills its own attribute of
``SourceNotes`` with ``model_copy(update=...)``. Notes never hold page content, queries,
proxy URLs or credentials, so they are safe to log, persist and show in reports.

This module imports nothing from ``rastro.domain.results`` so results can attach notes
without an import cycle.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from rastro.domain.capability_grants import Capability
from rastro.domain.sources import EGRESS_NAME, Contract

__all__ = [
    "CODE_PATTERN",
    "AcquisitionUse",
    "BlockKind",
    "BlockedHint",
    "BrowserFamily",
    "CrawlNote",
    "DiscoveryNote",
    "FieldCounts",
    "HttpVersion",
    "LadderLevel",
    "LadderStep",
    "RenderMode",
    "RepairNote",
    "SourceNotes",
    "SourceReason",
]

CODE_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
"""The shape of an issue code or failure detail: lowercase words joined by underscores."""
_DOCS_ANCHOR = r"^docs/[a-z0-9][a-z0-9_/-]{0,120}\.md(?:#[a-z0-9][a-z0-9-]{0,80})?$"

type SourceReason = Literal[
    "complete",
    "not_modified",
    "page_cap",
    "depth_cap",
    "request_budget",
    "time_budget",
    "stopped",
    "failed",
    "blocked",
    "robots_denied",
    "invalid_data",
    "missing_data",
]
"""Why work on a source ended."""
type BlockKind = Literal[
    "cloud_ip_refused",
    "login_wall",
    "captcha",
    "challenge_unsolved",
    "rate_limited",
    "robots",
    "geo_blocked",
    "unknown",
]
"""The kind of refusal a site gave to automated access."""
type LadderLevel = Literal[
    "http", "impersonation", "browser", "stealth", "challenge", "proxy_rotation", "solver_plugin"
]
"""One level of the acquisition ladder, from plain HTTP to an operator solver plugin."""
type RenderMode = Literal["http", "browser", "stealth"]
type BrowserFamily = Literal["chrome", "firefox", "safari", "edge"]
"""A browser whose network fingerprint the HTTP transport can present."""
type HttpVersion = Literal["1.1", "2", "3"]

FieldName = Annotated[str, Field(min_length=1, max_length=128)]
Count = Annotated[int, Field(ge=0)]
type FieldCounts = dict[FieldName, Count]
"""Per-field counters keyed by schema field name."""


def _empty(value: object) -> bool:
    return value is None or value == {} or value == ()


class BlockedHint(Contract):
    """A site refused access; ``docs`` is a repository-relative guide anchor."""

    kind: BlockKind
    http_status: int | None = Field(default=None, ge=100, le=599)
    docs: str = Field(pattern=_DOCS_ANCHOR)


class LadderStep(Contract):
    """One attempt at one ladder level and how it ended."""

    level: LadderLevel
    outcome: Literal["ok", "blocked", "failed", "not_granted"]


class AcquisitionUse(Contract):
    """How the source was fetched. Names and counts only; never proxy URLs or credentials."""

    render: RenderMode
    egress: str = Field(default="direct", pattern=EGRESS_NAME)
    proxy_rotation: bool = False
    proxies_used: Count = 0
    challenge_solved: bool = False
    impersonate: BrowserFamily | None = None
    http_version: HttpVersion | None = None
    robots: Literal["obeyed", "ignored", "overridden"] = "obeyed"
    ladder: tuple[LadderStep, ...] = Field(default=(), max_length=16)
    start_level: LadderLevel | None = None


class DiscoveryNote(Contract):
    """Where the source's pages came from and how many locations survived filtering."""

    seed: Literal["page", "sitemap"]
    order: Literal["document", "reverse", "lastmod"] = "document"
    locs_found: Count = 0
    locs_filtered: Count = 0
    locs_admitted: Count = 0


class CrawlNote(Contract):
    """The state of a crawl when it ended, including its checkpoint."""

    mode: Literal["bounded", "unbounded"]
    pages_fetched: Count = 0
    frontier_remaining: Count = 0
    resumed: bool = False
    checkpoint: Literal["none", "saved", "finished", "discarded"] = "none"
    stop: SourceReason = "complete"


class RepairNote(Contract):
    """Drift and repair for the source; ``promotion_refused`` is an issue code."""

    drift: Literal["none", "low", "elevated", "high"] | None = None
    candidate_version: int | None = Field(default=None, ge=1)
    decision: Literal["promoted", "quarantined", "rejected", "none"] = "none"
    promotion_refused: str | None = Field(default=None, max_length=64, pattern=CODE_PATTERN)
    origin: Literal["relocation", "adaptive", "model"] | None = None
    model_calls: Count = 0


class SourceNotes(Contract):
    """Every note about one source. Empty attributes are left out when serialized."""

    acquisition: AcquisitionUse | None = Field(default=None, exclude_if=_empty)
    discovery: DiscoveryNote | None = Field(default=None, exclude_if=_empty)
    crawl: CrawlNote | None = Field(default=None, exclude_if=_empty)
    repair: RepairNote | None = Field(default=None, exclude_if=_empty)
    blocked: BlockedHint | None = Field(default=None, exclude_if=_empty)
    fields_nulled: FieldCounts = Field(default_factory=dict, max_length=256, exclude_if=_empty)
    capabilities_used: tuple[Capability, ...] = Field(default=(), exclude_if=_empty)
