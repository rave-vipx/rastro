"""The one place a spider page is fetched.

Every document a spider run reads, pagination pages included, goes through
``AcquisitionPort.acquire`` here. The crawl of one start source runs inside that source's
``source_session()``: one browser context for all its pages, closed when the source ends. A
browser context is one page, so a browser crawl fetches (and scrolls) one page at a time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel

from rastro.application.browser_sessions import source_session
from rastro.application.outcomes import stamp_acquired
from rastro.application.pagination import extract_paginated
from rastro.application.scroll import NoteOnce, scrolled
from rastro.application.spider import extract_spider
from rastro.application.spider_links import LinkHarvest
from rastro.application.spider_sitemap import acquire_charged, fetch_sitemap
from rastro.application.spider_unbounded import CrawlRuntime
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import ExtractionResult, Limits, Recipe, Representation, Source
from rastro.domain.scroll import ScrollSpec
from rastro.domain.spider import SpiderPolicy
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.browser import BrowserMayRender, BrowserRendered
from rastro.ports.engine import CrawlThrottle
from rastro.ports.parser import DocumentParser


async def fetch_spider_page[T: BaseModel](
    page: Source,
    *,
    schema: type[T],
    recipe: Recipe,
    parser: DocumentParser,
    limits: Limits,
    acquisition: AcquisitionPort,
    policy: AcquisitionPolicy | None,
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
) -> ExtractionResult[T]:
    """Fetch and extract one spider page; the run's shared scope bounds every request."""
    if recipe.pagination is not None:
        return await extract_paginated(
            source=page,
            schema=schema,
            recipe=recipe,
            parser=parser,
            limits=limits,
            acquisition=acquisition,
            policy=policy,
            extract_page=extract_page,
        )
    started, started_at = perf_counter(), datetime.now(UTC)
    fetched = await acquire_charged(
        lambda: acquisition.acquire(
            page,
            max_bytes=limits.max_input_bytes(recipe.representation),
            representation=recipe.representation,
        )
    )
    result = await extract_page(fetched)
    return stamp_acquired(
        result, source=page, fetched=fetched, started=started, started_at=started_at
    )


def _throttle(spider: SpiderPolicy) -> CrawlThrottle | None:
    """An unbounded crawl adapts its pace per host; a bounded crawl keeps its fixed interval."""
    if not spider.unbounded:
        return None
    from rastro.adapters.scrapling_throttle import default_throttle

    return default_throttle()


def _may_render_in_browser(acquisition: AcquisitionPort, url: str) -> bool:
    """Any page of the crawl may share one browser page, so pages must not overlap."""
    if isinstance(acquisition, BrowserMayRender):
        return acquisition.may_render_in_browser(url)
    return isinstance(acquisition, BrowserRendered) and acquisition.renders_in_browser(url)


async def crawl_source[T: BaseModel](
    source: Source,
    *,
    schema: type[T],
    spider: SpiderPolicy,
    recipe: Recipe,
    parser: DocumentParser,
    limits: Limits,
    acquisition: AcquisitionPort,
    policy: AcquisitionPolicy | None,
    representation: Representation,
    extract_fetched: Callable[[AcquisitionResult, Source], Awaitable[ExtractionResult[T]]],
    scroll: ScrollSpec | None = None,
) -> ExtractionResult[T]:
    """Spider ``source``: every page is fetched by ``fetch_spider_page`` and harvested."""
    note = NoteOnce()

    def fetch(page: Source, harvest: LinkHarvest) -> Awaitable[ExtractionResult[T]]:
        async def extract_one(fetched: AcquisitionResult) -> ExtractionResult[T]:
            result = await extract_fetched(fetched, page)
            harvest.add(fetched.url, fetched.body, result, representation)
            return result

        extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]] = extract_one
        if scroll is not None:  # steps spend the crawl's page and request budgets
            extract_page = scrolled(
                extract_one,
                spec=scroll,
                acquisition=acquisition,
                limits=limits,
                owns_scope=False,
                note=note,
            )

        return fetch_spider_page(
            page,
            schema=schema,
            recipe=recipe,
            parser=parser,
            limits=limits,
            acquisition=acquisition,
            policy=policy,
            extract_page=extract_page,
        )

    def seed(first: bool, page: Source, harvest: LinkHarvest) -> Awaitable[ExtractionResult[T]]:
        return fetch_sitemap(
            page, harvest, first=first, schema=schema, limits=limits, acquisition=acquisition
        )

    in_browser = _may_render_in_browser(acquisition, source.url)
    async with source_session():  # one browser context per start source, closed after it
        return await extract_spider(
            source=source,
            schema=schema,
            spider=spider,
            policy=policy,
            parser=parser,
            limits=limits,
            sequential=recipe.pagination is not None or in_browser,
            fetch_page=fetch,
            fetch_seed=seed if spider.seed == "sitemap" else None,
            runtime=CrawlRuntime(throttle=_throttle(spider)),
        )
