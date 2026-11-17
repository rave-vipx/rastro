"""Bounded breadth-first spider over one start source (N3; SEC-001, SEC-003, SEC-008).

One request budget and one document budget live in ``PAGINATION_SCOPE``, so pagination
inside a spider page spends the same numbers. Pages are fetched through ``fetch_page``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial

from pydantic import BaseModel

from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    PaginationScope,
)
from rastro.application.outcomes import failure_result
from rastro.application.scroll import PAGE_COUNTER, PageCounter, pages_left, spend_page
from rastro.application.spider_filter import link_refusal
from rastro.application.spider_links import (
    Link,
    LinkHarvest,
    classify_link,
    describe_url,
    report_path,
)
from rastro.application.spider_outcome import PAGE_FAILURES, finish_crawl, page_outcome
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.domain.spider import SpiderPolicy
from rastro.domain.spider_report import SpiderOutcome, SpiderPage, SpiderReport
from rastro.ports.parser import DocumentParser

_NOTE_CAP = 500  # discovered-but-not-fetched rows; fetched pages (max 100) always have a row
type FetchPage[T: BaseModel] = Callable[[Source, LinkHarvest], Awaitable[ExtractionResult[T]]]
# A sitemap seed (N16): the bool says whether this is the start sitemap (an index may nest once).
type FetchSeed[T: BaseModel] = Callable[[bool, Source, LinkHarvest], Awaitable[ExtractionResult[T]]]


async def extract_spider[T: BaseModel](
    *,
    source: Source,
    schema: type[T],
    spider: SpiderPolicy,
    policy: AcquisitionPolicy | None,
    parser: DocumentParser,
    limits: Limits,
    sequential: bool,
    fetch_page: FetchPage[T],
    fetch_seed: FetchSeed[T] | None = None,
) -> ExtractionResult[T]:
    """Crawl from ``source`` and combine every page's records like pagination does.

    With ``fetch_seed``, the start URL and any child sitemaps are depth 0 and their locs
    are the depth-1 links; they spend the page and request caps like any document.
    """
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
        return await crawl.run(start, workers, fetch_seed)
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
        fetch_page: FetchPage[T],
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

    async def run(
        self, start: Link, workers: int, fetch_seed: FetchSeed[T] | None
    ) -> ExtractionResult[T]:
        self.seen.add(start.url)
        frontier, first = [(1, start)], 0
        if fetch_seed is not None:
            frontier, first = await self._seed(start, fetch_seed), 1
        for depth in range(first, self.spider.max_depth + 1):
            batch = self._admit(frontier, depth)
            fetched = await self._fetch_all(batch, workers)
            frontier = []
            for (ordinal, link), (result, harvest) in zip(batch, fetched, strict=True):
                self._record(ordinal, depth, link, result, harvest)
                self._discover(harvest.hrefs, depth + 1, frontier)
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

    async def _seed(self, start: Link, fetch_seed: FetchSeed[T]) -> list[tuple[int, Link]]:
        """Fetch the start sitemap and, for an index, its children; return the depth-1 links."""
        frontier: list[tuple[int, Link]] = []
        sitemaps = [(1, start)]
        for ordinal, link in sitemaps:  # children are appended while the start is read
            if not self._admit([(ordinal, link)], 0):
                continue
            result, harvest = await self._fetch(ordinal, link, partial(fetch_seed, ordinal == 1))
            self._record(ordinal, 0, link, result, harvest, seed=True)
            self._discover(harvest.sitemaps, 0, sitemaps, paths=False)  # host rules only
            self._discover(harvest.hrefs, 1, frontier)
        return frontier

    async def _fetch(
        self, ordinal: int, link: Link, fetch: FetchPage[T] | None = None
    ) -> tuple[ExtractionResult[T], LinkHarvest]:
        harvest = LinkHarvest(self.spider, self.parser, self.limits)
        page_source = Source.model_validate({**self.source.model_dump(), "url": link.url})
        try:
            return await (fetch or self.fetch_page)(page_source, harvest), harvest
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
        *,
        seed: bool = False,
    ) -> None:
        outcome = page_outcome(result)
        if outcome == "no_records" and not seed:  # a seed keeps ``sitemap_empty``
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

    def _discover(
        self, hrefs: list[str], depth: int, frontier: list[tuple[int, Link]], *, paths: bool = True
    ) -> None:
        """Queue each new link found for ``depth``, or note why it is not fetched."""
        for href in hrefs:
            link = classify_link(self.source.url, href)
            if not link.refusal and link.url in self.seen:
                continue
            self.ordinal += 1
            refusal: SpiderOutcome | None = link.refusal or link_refusal(
                link, self.scope, self.spider, self.policy, paths=paths
            )
            if refusal is None and depth > self.spider.max_depth:
                refusal = "depth_limit"
                self.hit.add("depth_limit")
            if refusal is not None:
                self._note(self.ordinal, depth, link, refusal)
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
        report = SpiderReport(
            pages=tuple(self.rows[key] for key in sorted(self.rows)),
            discovered=self.ordinal,
            unreported=self.unreported,
        )
        return finish_crawl(self.pages, self.hit, self.limits, report, self.links_failed)
