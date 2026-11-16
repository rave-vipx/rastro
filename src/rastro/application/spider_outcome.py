"""What became of one spider page, from its result's status and issue codes (N3)."""

from __future__ import annotations

from pydantic import BaseModel

from rastro.domain.models import ExtractionResult, Outcome
from rastro.domain.spider_report import SpiderOutcome

# For a page that was fetched, a boundary outcome means its redirect was refused.
PAGE_FAILURES = frozenset({"failed", "partial", "robots_denied", "off_host", "https_downgrade"})


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
