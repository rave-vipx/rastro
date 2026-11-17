"""Fetch one sitemap of a sitemap-seeded spider (N16; SEC-001, SEC-003, SEC-008).

The sitemap goes through the same acquisition port as every page: allowlist, robots,
pacing, DNS checks, egress, the request budget and the byte cap. Its ``<loc>`` values,
in document order, become the crawl's depth-1 links; it yields no records itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel

from rastro.application.acquisition_state import PAGE_CHARGES, PAGINATION_SCOPE
from rastro.application.outcomes import failure_result, stamp_acquired
from rastro.application.sitemap import SitemapError, parse_sitemap
from rastro.application.spider_links import LinkHarvest
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import ExtractionResult, Limits, Outcome, Source
from rastro.ports.acquisition import AcquisitionPort, XmlAcquisition


async def acquire_charged(fetch: Callable[[], Awaitable[AcquisitionResult]]) -> AcquisitionResult:
    """Run one acquisition; an acquirer that does not charge the run's scope is still counted."""
    scope = PAGINATION_SCOPE.get()
    charges = [0]
    token = PAGE_CHARGES.set(charges)
    try:
        fetched = await fetch()
    finally:
        PAGE_CHARGES.reset(token)
    if scope is not None:
        scope.remaining -= max(0, fetched.requests - charges[0])
    return fetched


async def fetch_sitemap[T: BaseModel](
    page: Source,
    harvest: LinkHarvest,
    *,
    first: bool,
    schema: type[T],
    limits: Limits,
    acquisition: AcquisitionPort,
) -> ExtractionResult[T]:
    """Fetch and parse ``page``; only the ``first`` sitemap may be an index."""
    started, started_at = perf_counter(), datetime.now(UTC)

    def fail(status: Outcome, code: str) -> ExtractionResult[T]:
        return failure_result(
            status=status, issue_code=code, source=page, schema=schema, started_at=started_at
        )

    if not isinstance(acquisition, XmlAcquisition):
        return fail(Outcome.PERMANENT_FAILURE, "sitemap_unsupported")
    max_bytes = limits.max_input_bytes("html")
    fetched = await acquire_charged(lambda: acquisition.acquire_xml(page, max_bytes=max_bytes))
    result = _read(fetched, harvest, first, max_bytes, fail)
    return stamp_acquired(
        result, source=page, fetched=fetched, started=started, started_at=started_at
    )


def _read[T: BaseModel](
    fetched: AcquisitionResult,
    harvest: LinkHarvest,
    first: bool,
    max_bytes: int,
    fail: Callable[[Outcome, str], ExtractionResult[T]],
) -> ExtractionResult[T]:
    if fetched.status != Outcome.SUCCESS or fetched.raw_body is None:
        return fail(fetched.status, fetched.issue_code or "acquisition_failed")
    try:
        sitemap = parse_sitemap(fetched.raw_body, max_bytes)
    except SitemapError as error:
        status = (
            Outcome.INVALID_DATA if error.code == "sitemap_invalid" else Outcome.PERMANENT_FAILURE
        )
        return fail(status, error.code)
    if sitemap.index and not first:
        return fail(Outcome.INVALID_DATA, "sitemap_nested_index")  # one level at most
    empty = fail(Outcome.MISSING_DATA, "sitemap_empty")
    if not sitemap.locs:
        return empty
    (harvest.sitemaps if sitemap.index else harvest.hrefs).extend(sitemap.locs)
    return empty.model_copy(update={"issues": ()})  # links only, like a listing page
