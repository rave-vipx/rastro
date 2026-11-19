"""What became of one spider page, from its result's status and issue codes (N3)."""

from __future__ import annotations

from pydantic import BaseModel

from rastro.application.pagination_records import combine_pages
from rastro.domain.models import ExtractionResult, Issue, Limits, Outcome
from rastro.domain.spider_report import SpiderOutcome, SpiderReport

# For a page that was fetched, a boundary outcome means its redirect was refused.
PAGE_FAILURES = frozenset({"failed", "partial", "robots_denied", "off_host", "https_downgrade"})
_LIMITS = ("budget", "page_limit", "depth_limit")  # a hit cap: partial, in this order


def page_outcome[T: BaseModel](result: ExtractionResult[T]) -> SpiderOutcome:
    """What became of one fetched page, from the result's own status and issue codes."""
    codes = {issue.code for issue in result.issues}
    if "robots_denied" in codes:
        return "robots_denied"
    if "request_limit" in codes:
        return "budget"
    if "off_host" in codes:  # a redirect left the boundary
        return "off_host"
    if "https_downgrade" in codes:
        return "https_downgrade"
    if result.status == Outcome.SUCCESS:
        return "fetched"
    if result.status == Outcome.PARTIAL_SUCCESS:
        return "partial"
    if result.status == Outcome.MISSING_DATA and not result.records:
        return "no_records"
    return "failed"


def finish_crawl[T: BaseModel](
    pages: list[ExtractionResult[T]],
    hit: set[str],
    limits: Limits,
    report: SpiderReport,
    links_failed: bool,
    stopped: str | None = None,
) -> ExtractionResult[T]:
    """Combine the pages; a time or record stop, else the first cap hit or failed page."""
    stop = {"time_budget": "spider_time_budget", "record_limit": "record_limit"}.get(stopped or "")
    stop = stop or next((f"spider_{name}" for name in _LIMITS if name in hit), None)
    if stop is None:
        stop = "spider_page_failed" if "page_failed" in hit else "spider_complete"
    combined = combine_pages(pages, stop, limits)
    issues = tuple(  # the crawl's own code for a spent time budget (docs/issues.md)
        Issue(code="crawl_time_budget") if issue.code == "spider_time_budget" else issue
        for issue in combined.issues
    )
    if links_failed:
        issues = (*issues, Issue(code="spider_links_failed"))
    status = combined.status
    if not combined.records and status == Outcome.SUCCESS:
        status = Outcome.MISSING_DATA  # links alone are not a successful extraction
    return combined.model_copy(update={"spider": report, "status": status, "issues": issues})
