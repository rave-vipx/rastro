"""Bounded breadth-first spider over one start source (N3; SEC-001, SEC-003, SEC-008).

One request budget and one document budget live in ``PAGINATION_SCOPE``, so pagination
inside a spider page spends the same numbers. Pages are fetched through ``fetch_page``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    PaginationScope,
)
from rastro.application.outcomes import failure_result
from rastro.application.pagination_records import combine_pages
from rastro.application.scroll import PAGE_COUNTER, PageCounter, pages_left, spend_page
from rastro.application.spider_filter import link_refusal
from rastro.application.spider_links import (
    Link,
    LinkHarvest,
    classify_link,
    describe_url,
    report_path,
)
from rastro.application.spider_outcome import PAGE_FAILURES, page_outcome
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Issue,
    Limits,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.domain.spider import SpiderPolicy
from rastro.domain.spider_report import SpiderOutcome, SpiderPage, SpiderReport
from rastro.ports.parser import DocumentParser

_NOTE_CAP = 500  # discovered-but-not-fetched rows; fetched pages (max 100) always have a row
_LIMITS = ("budget", "page_limit", "depth_limit")  # a hit cap: partial, in this order


async def extract_spider[T: BaseModel](
    *,
    source: Source,
    schema: type[T],
    spider: SpiderPolicy,
    policy: AcquisitionPolicy | None,
    parser: DocumentParser,
    limits: Limits,
    sequential: bool,
    fetch_page: Callable[[Source, LinkHarvest], Awaitable[ExtractionResult[T]]],
) -> ExtractionResult[T]:
    """Crawl from ``source`` and combine every page's records like pagination does."""
    start = describe_url(source.url)
    if start.refusal:
        raise ConfigurationError("Spider start URL must be a plain http(s) URL")
    scope = PaginationScope(
        host=start.host,
        secure=source.url.lower().startswith("https:"),
        remaining=policy.max_requests if policy else 30,
        hosts=frozenset()
        if spider.same_host
        else frozenset(policy.allowed_hosts if policy else ()),
        total=policy.max_requests if policy else 30,
    )
    token = PAGINATION_SCOPE.set(scope)
    pages = PAGE_COUNTER.set(PageCounter(spider.max_pages))  # scroll steps spend it too
    try:
        crawl = _Crawl(source, schema, spider, parser, limits, fetch_page, scope, policy)
        workers = 1 if sequential or policy is None else policy.max_concurrency
        return await crawl.run(start, workers)
    finally:
        PAGE_COUNTER.reset(pages)
        PAGINATION_SCOPE.reset(token)


class _Crawl[T: BaseModel]:
    def __init__(
        self,
        source: Source,
        schema: type[T],
        spider: SpiderPolicy,
        parser: DocumentParser,
        limits: Limits,
        fetch_page: Callable[[Source, LinkHarvest], Awaitable[ExtractionResult[T]]],
        scope: PaginationScope,
        policy: AcquisitionPolicy | None,
    ) -> None:
        self.policy = policy
        self.source, self.schema, self.spider = source, schema, spider
        self.parser, self.limits, self.fetch_page, self.scope = parser, limits, fetch_page, scope
        self.rows: dict[int, SpiderPage] = {}
        self.pages: list[ExtractionResult[T]] = []
        self.seen: set[str] = set()
        self.hit: set[str] = set()
        self.ordinal = 1
        self.unreported = 0
        self.links_failed = False
        self.started_at = datetime.now(UTC)

    async def run(self, start: Link, workers: int) -> ExtractionResult[T]:
        self.seen.add(start.url)
        frontier = [(1, start)]
        for depth in range(self.spider.max_depth + 1):
            batch = self._admit(frontier, depth)
            fetched = await self._fetch_all(batch, workers)
            frontier = []
            for (ordinal, link), (result, harvest) in zip(batch, fetched, strict=True):
                self._record(ordinal, depth, link, result, harvest)
                self._discover(harvest, depth, frontier)
            if not frontier:
                break
        return self._finish()

    def _admit(self, frontier: list[tuple[int, Link]], depth: int) -> list[tuple[int, Link]]:
        batch: list[tuple[int, Link]] = []
        for ordinal, link in frontier:
            if self.scope.remaining <= 0:
                self._note(ordinal, depth, link, "budget")
            elif not spend_page():
                self._note(ordinal, depth, link, "page_limit")
            else:
                batch.append((ordinal, link))
        return batch

    async def _fetch_all(
        self, batch: list[tuple[int, Link]], workers: int
    ) -> list[tuple[ExtractionResult[T], LinkHarvest]]:
        results: list[tuple[ExtractionResult[T], LinkHarvest] | None] = [None] * len(batch)
        pending = iter(enumerate(batch))

        async def worker() -> None:
            for index, (ordinal, link) in pending:
                results[index] = await self._fetch(ordinal, link)

        if workers == 1 or len(batch) <= 1:
            await worker()
        else:
            async with asyncio.TaskGroup() as group:
                for _ in range(min(len(batch), workers)):
                    group.create_task(worker())
        return [item for item in results if item is not None]

    async def _fetch(self, ordinal: int, link: Link) -> tuple[ExtractionResult[T], LinkHarvest]:
        harvest = LinkHarvest(self.spider, self.parser, self.limits)
        page_source = Source.model_validate({**self.source.model_dump(), "url": link.url})
        try:
            return await self.fetch_page(page_source, harvest), harvest
        except (ConfigurationError, ResourceLimitError):
            if ordinal == 1:
                raise  # the start page fails exactly as it would without a spider
            failed = failure_result(
                status=Outcome.PERMANENT_FAILURE,
                issue_code="spider_page_failed",
                source=page_source,
                schema=self.schema,
                started_at=self.started_at,
            )
            return failed, harvest

    def _record(
        self,
        ordinal: int,
        depth: int,
        link: Link,
        result: ExtractionResult[T],
        harvest: LinkHarvest,
    ) -> None:
        outcome = page_outcome(result)
        if outcome == "no_records":
            result = result.model_copy(update={"issues": ()})
        if outcome == "budget" or result.pagination_stop == "budget":
            self.hit.add("budget")
        if result.pagination_stop == "max_pages" and not pages_left():
            self.hit.add("page_limit")  # pagination ran into the spider's page cap
        if outcome in PAGE_FAILURES or harvest.failed:
            self.hit.add("page_failed")
        self.links_failed |= harvest.failed
        self.rows[ordinal] = SpiderPage(
            ordinal=ordinal,
            depth=depth,
            host=link.host,
            path=report_path(link),
            outcome=outcome,
            records=len(result.records),
        )
        self.pages.append(result)

    def _discover(self, harvest: LinkHarvest, depth: int, frontier: list[tuple[int, Link]]) -> None:
        for href in harvest.hrefs:
            link = classify_link(self.source.url, href)
            if not link.refusal and link.url in self.seen:
                continue
            self.ordinal += 1
            refusal: SpiderOutcome | None = link.refusal or link_refusal(
                link, self.scope, self.spider, self.policy
            )
            if refusal is None and depth + 1 > self.spider.max_depth:
                refusal = "depth_limit"
                self.hit.add("depth_limit")
            if refusal is not None:
                self._note(self.ordinal, depth + 1, link, refusal)
            else:
                frontier.append((self.ordinal, link))
            if not link.refusal:
                self.seen.add(link.url)

    def _note(self, ordinal: int, depth: int, link: Link, outcome: SpiderOutcome) -> None:
        if outcome in ("budget", "page_limit"):
            self.hit.add(outcome)
        if len(self.rows) >= _NOTE_CAP:
            self.unreported += 1
            return
        self.rows[ordinal] = SpiderPage(
            ordinal=ordinal, depth=depth, host=link.host, path=report_path(link), outcome=outcome
        )

    def _finish(self) -> ExtractionResult[T]:
        stop = next((f"spider_{name}" for name in _LIMITS if name in self.hit), None)
        if stop is None:
            stop = "spider_page_failed" if "page_failed" in self.hit else "spider_complete"
        combined = combine_pages(self.pages, stop, self.limits)
        issues = combined.issues
        if self.links_failed:
            issues = (*issues, Issue(code="spider_links_failed"))
        status = combined.status
        if not combined.records and status == Outcome.SUCCESS:
            status = Outcome.MISSING_DATA  # links alone are not a successful extraction
        report = SpiderReport(
            pages=tuple(self.rows[key] for key in sorted(self.rows)),
            discovered=self.ordinal,
            unreported=self.unreported,
        )
        return combined.model_copy(update={"spider": report, "status": status, "issues": issues})
