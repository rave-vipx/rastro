"""Facts a run report states about one source, derived from its result: never content."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from rastro.domain.failure_reason import failure_detail
from rastro.domain.models import ExtractionResult, Outcome
from rastro.domain.provenance_url import provenance_url
from rastro.domain.source_notes import AcquisitionUse, SourceReason

__all__ = [
    "OK",
    "acquisition",
    "aware",
    "detail",
    "location",
    "pages",
    "reason",
    "rejected_by_code",
]

OK = (Outcome.SUCCESS, Outcome.UNCHANGED)
_INVALID = frozenset(
    {
        "validation_error",
        "invalid_jsonld",
        "invalid_json",
        "invalid_record",
        "invalid_record_path",
        "invalid_text",
    }
)
_STOPS: dict[str, SourceReason] = {
    "max_pages": "page_cap",
    "spider_page_limit": "page_cap",
    "spider_depth_limit": "depth_cap",
    "budget": "request_budget",
    "spider_budget": "request_budget",
    "record_limit": "stopped",
    "page_failure": "failed",
    "spider_page_failed": "failed",
}
_ATTEMPTED = ("fetched", "no_records", "partial", "failed")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.:\[\]-]{0,252}[a-z0-9\]])?$")


def reason(result: ExtractionResult[Any], outcome: Outcome) -> SourceReason:
    stop = _STOPS.get(result.pagination_stop or "")
    if stop is not None:
        return stop
    crawl = result.notes.crawl if result.notes else None
    if crawl is not None and crawl.stop != "complete":
        return crawl.stop
    codes = {issue.code for issue in result.issues}
    if "robots_denied" in codes:
        return "robots_denied"
    if outcome == Outcome.ACCESS_RESTRICTION or (result.notes and result.notes.blocked):
        return "blocked"
    if outcome == Outcome.UNCHANGED:
        return "not_modified"
    if outcome in (Outcome.INVALID_DATA, Outcome.INSUFFICIENT_EVIDENCE):
        return "invalid_data"
    if outcome == Outcome.MISSING_DATA:
        return "missing_data"
    if outcome in (Outcome.TRANSIENT_FAILURE, Outcome.PERMANENT_FAILURE):
        return "failed"
    return (
        "invalid_data"
        if outcome == Outcome.PARTIAL_SUCCESS and result.metrics.rejected
        else "complete"
    )


def detail(result: ExtractionResult[Any]) -> str | None:
    """``failure_detail`` plus ``http_<status>`` for any other refused or failed response."""
    if result.status in (*OK, Outcome.PARTIAL_SUCCESS):
        return None
    base = failure_detail(result)
    status = result.http_status
    if base in {"robots_denied", "timeout"} or (base and re.fullmatch(r"http_\d{3}", base)):
        return base
    return f"http_{status}" if status is not None and status >= 400 else base


def pages(result: ExtractionResult[Any]) -> int:
    if result.spider is not None:
        return sum(page.outcome in _ATTEMPTED for page in result.spider.pages)
    if result.notes and result.notes.crawl:
        return result.notes.crawl.pages_fetched
    return max((r.page_index or 1 for r in result.records), default=1)


def rejected_by_code(result: ExtractionResult[Any]) -> dict[str, int]:
    """One count per rejected record: its first invalid-data issue code."""
    first: dict[int, str] = {}
    for issue in result.issues:
        if issue.record_index is not None and issue.code in _INVALID:
            first.setdefault(issue.record_index, issue.code)
    counts: dict[str, int] = {}
    for code in first.values():
        counts[code] = counts.get(code, 0) + 1
    return counts


def acquisition(result: ExtractionResult[Any]) -> AcquisitionUse | None:
    if result.metrics.acquisition_method != "http":
        return None
    return AcquisitionUse(
        render="http",
        egress=result.metrics.egress,
        robots="overridden" if result.metrics.robots_overridden else "obeyed",
    )


def location(url: str) -> tuple[str, str]:
    """Host and path of ``url`` in provenance form: never userinfo, query or fragment."""
    parts = urlsplit(provenance_url(url))
    host = (parts.netloc or "").lower()
    path = quote(parts.path or "/", safe="/%")[:2047]
    return (host if _HOST.fullmatch(host) else "unknown"), path if path.startswith("/") else "/"


def aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
