"""Fetch one sitemap of a sitemap-seeded spider.

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
from rastro.application.scroll import PAGE_COUNTER
from rastro.application.sitemap import SitemapError, parse_sitemap
from rastro.application.spider_filter import link_refusal
from rastro.application.spider_links import LinkHarvest, classify_link
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import ExtractionResult, Limits, Outcome, Source
from rastro.domain.provenance_url import page_identity
from rastro.domain.source_notes import DiscoveryNote, SourceNotes
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
    result = _read(page.url, fetched, harvest, first, max_bytes, fail)
    return stamp_acquired(
        result, source=page, fetched=fetched, started=started, started_at=started_at
    )


def _read[T: BaseModel](
    base_url: str,
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
    if not sitemap.entries:
        return empty
    (harvest.sitemaps if sitemap.index else harvest.seed_entries).extend(sitemap.entries)
    note = _discovery_for_sitemap(base_url, harvest, sitemap.index, first)
    notes = harvest.bind_root_notes(
        (empty.notes or SourceNotes()).model_copy(update={"discovery": note}),
        first=first,
    )
    return empty.model_copy(
        update={"issues": (), "notes": notes}
    )  # links only, like a listing page


def _discovery_for_sitemap(
    base_url: str, harvest: LinkHarvest, is_index: bool, first: bool
) -> DiscoveryNote:
    scope = PAGINATION_SCOPE.get()
    scope_id = id(scope) if scope is not None else id(harvest)
    if is_index:
        harvest.record_seed_entries(scope_id, (), reset=first)
        return harvest.record_discovery(found=0, filtered=0, admitted=0)
    entries = harvest.record_seed_entries(scope_id, tuple(harvest.seed_entries), reset=first)
    spider = harvest.spider
    counter = PAGE_COUNTER.get()
    seen: set[str] = {page_identity(base_url)}
    found = len(entries)
    filtered = 0
    surviving = 0
    for entry in entries:
        link = classify_link(base_url, entry.loc)
        if not link.refusal and page_identity(link.url) in seen:
            continue
        if not link.refusal:
            seen.add(page_identity(link.url))
        refusal = link.refusal
        if refusal is None and scope is not None:
            refusal = link_refusal(link, scope, spider, None, paths=True)
        if refusal is not None:
            filtered += 1
        else:
            surviving += 1
    slots = surviving
    if counter is not None:
        slots = min(slots, max(0, counter.remaining))
    if scope is not None:
        slots = min(slots, max(0, scope.remaining))
    return harvest.record_discovery(
        found=found,
        filtered=filtered,
        admitted=min(surviving, slots),
    )
