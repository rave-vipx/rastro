"""Breadth-first spider over one start source (N3; SEC-001, SEC-003, SEC-008).

One request budget and one document budget live in ``PAGINATION_SCOPE``, so pagination
inside a spider page spends the same numbers. Pages are fetched through ``fetch_page``.
An unbounded crawl replaces both budgets with its time budget (``spider_unbounded``).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial

from pydantic import BaseModel

from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope
from rastro.application.crawl_checkpoint import checkpoint_store, seen_key
from rastro.application.outcomes import failure_result
from rastro.application.pagination_records import with_page_provenance
from rastro.application.scroll import PAGE_COUNTER, PageCounter, pages_left, spend_page
from rastro.application.sitemap import SitemapEntry, order_entries
from rastro.application.spider_frontier import CrawlRows, Frontier, Queued, in_order
from rastro.application.spider_links import Link, LinkHarvest, describe_url
from rastro.application.spider_outcome import PAGE_FAILURES, finish_crawl, page_outcome
from rastro.application.spider_unbounded import (
    CHUNK_PER_WORKER,
    UNBOUNDED,
    CrawlCheckpointing,
    CrawlRuntime,
    Stop,
    rebase_on_start,
    records_full,
    with_crawl_note,
)
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
from rastro.ports.parser import DocumentParser

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
    runtime: CrawlRuntime | None = None,
) -> ExtractionResult[T]:
    """Crawl from ``source`` and combine every page's records like pagination does.

    With ``fetch_seed``, the start URL and any child sitemaps are depth 0 and their locs
    are the depth-1 links; they spend the page and request caps like any document.
    """
    start = describe_url(source.url)
    if start.refusal:
        raise ConfigurationError("Spider start URL must be a plain http(s) URL")
    budget = UNBOUNDED if spider.unbounded else (policy.max_requests if policy else 30)
    scope = PaginationScope(
        host=start.host,
        secure=source.url.lower().startswith("https:"),
        remaining=budget,
        hosts=frozenset()
        if spider.same_host
        else frozenset(policy.allowed_hosts if policy else ()),
        total=budget,
    )
    cap = spider.max_pages if isinstance(spider.max_pages, int) else UNBOUNDED
    token = PAGINATION_SCOPE.set(scope)
    pages = PAGE_COUNTER.set(PageCounter(cap))  # scroll steps spend it too
    try:
        crawl = _Crawl(source, schema, spider, parser, limits, fetch_page, scope, policy)
        crawl.runtime = runtime or CrawlRuntime()
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
        self.rows = CrawlRows()
        self.frontier = Frontier(source.url, spider, scope, policy, self.rows)
        self.pages: list[ExtractionResult[T]] = []
        self.links: list[tuple[int, Link]] = []  # depth and link of each page in ``pages``
        self.links_failed = False
        self.started_at = datetime.now(UTC)
        self.runtime = CrawlRuntime()
        self.stopped: Stop | None = None
        self.checkpointing = CrawlCheckpointing(checkpoint_store(source.url, spider))

    async def run(
        self, start: Link, workers: int, fetch_seed: FetchSeed[T] | None
    ) -> ExtractionResult[T]:
        deadline = self.runtime.clock() + self.spider.time_budget_seconds
        self.frontier.seen.add(seen_key(start.url))
        queue = self._resume()
        if not queue:
            queue = deque([(1, 0, start)])
            if fetch_seed is not None:
                queue = deque(await self._seed(start, fetch_seed))
        size = workers * CHUNK_PER_WORKER if self.spider.unbounded else 0
        chunks = 0
        while queue:
            if chunks and self.runtime.clock() >= deadline:
                self.stopped = "time_budget"
                break
            chunk = [queue.popleft() for _ in range(min(size or len(queue), len(queue)))]
            batch = self._admit(chunk)
            fetched = await in_order([(o, link) for o, _, link in batch], workers, self._fetch)
            for (ordinal, depth, link), (result, harvest) in zip(batch, fetched, strict=True):
                self._record(ordinal, depth, link, result, harvest)
                self.frontier.discover(harvest.hrefs, depth + 1, queue)
            chunks += 1
            if self.spider.unbounded and records_full(self.pages, self.links, self.limits, queue):
                self.stopped = "record_limit"
                break
        return self._finish(list(queue))

    def _resume(self) -> deque[Queued]:
        checkpoint = self.checkpointing.load()
        queue = self.frontier.resume(checkpoint) if checkpoint is not None else deque()
        self.checkpointing.resumed = bool(queue)
        return queue

    def _admit(self, queued: list[Queued]) -> list[Queued]:
        batch: list[Queued] = []
        for ordinal, depth, link in queued:
            if self.scope.remaining <= 0:
                self.rows.note(ordinal, depth, link, "budget")
            elif not spend_page():
                self.rows.note(ordinal, depth, link, "page_limit")
            else:
                batch.append((ordinal, depth, link))
        return batch

    async def _seed(self, start: Link, fetch_seed: FetchSeed[T]) -> list[Queued]:
        """Fetch the start sitemap and, for an index, its children; return the depth-1 links."""
        frontier: list[Queued] = []
        sitemaps: list[Queued] = [(1, 0, start)]
        entries: list[SitemapEntry] = []
        order = self.spider.seed_order
        for ordinal, _, link in sitemaps:  # children are appended while the start is read
            if not self._admit([(ordinal, 0, link)]):
                continue
            result, harvest = await self._fetch(ordinal, link, partial(fetch_seed, ordinal == 1))
            self._record(ordinal, 0, link, result, harvest, seed=True)
            children = order_entries(harvest.sitemaps, order)
            self.frontier.discover([item.loc for item in children], 0, sitemaps, paths=False)
            entries.extend(
                reversed(harvest.seed_entries) if order == "reverse" else harvest.seed_entries
            )
        ordered = order_entries(entries, "lastmod") if order == "lastmod" else entries
        self.frontier.discover([item.loc for item in ordered], 1, frontier)
        return frontier

    async def _fetch(
        self, ordinal: int, link: Link, fetch: FetchPage[T] | None = None
    ) -> tuple[ExtractionResult[T], LinkHarvest]:
        harvest = LinkHarvest(self.spider, self.parser, self.limits)
        page_source = Source.model_validate({**self.source.model_dump(), "url": link.url})
        floor = self.policy.min_interval_seconds if self.policy else 0.0
        try:
            call = partial(fetch or self.fetch_page, page_source, harvest)
            return await self.runtime.paced(link.host, floor, call), harvest
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
        hit = self.rows.hit
        if outcome == "budget" or result.pagination_stop == "budget":
            hit.add("budget")
        if result.pagination_stop == "max_pages" and not pages_left():
            hit.add("page_limit")  # pagination ran into the spider's page cap
        if outcome in PAGE_FAILURES or harvest.failed:
            hit.add("page_failed")
        self.links_failed |= harvest.failed
        self.rows.fetched(ordinal, depth, link, outcome, len(result.records))
        self.pages.append(with_page_provenance(result, harvest.page_url or link.url))
        self.links.append((depth, link))

    def _finish(self, queue: list[Queued]) -> ExtractionResult[T]:
        report = self.rows.report(self.frontier.ordinal)
        result = finish_crawl(
            self.pages, self.rows.hit, self.limits, report, self.links_failed, self.stopped
        )
        if self.checkpointing.resumed:  # records belong to the start URL, as in the first run
            result = rebase_on_start(result, self.source)
        remaining = [(depth, link.url) for _, depth, link in queue]
        return with_crawl_note(
            result,
            unbounded=self.spider.unbounded,
            pages=len(self.pages),
            frontier=len(remaining),
            checkpointing=self.checkpointing,
            checkpoint=self.checkpointing.close(remaining, self.frontier.seen),
        )
