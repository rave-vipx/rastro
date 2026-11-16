"""The one place a spider page is fetched (N3).

Every document a spider run reads, pagination pages included, goes through
``AcquisitionPort.acquire`` here. A per-source acquisition scope (one browser context for
all pages of a source) therefore applies to spider pages without touching the crawl.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel

from rastro.application.acquisition_state import PAGINATION_SCOPE
from rastro.application.outcomes import stamp_acquired
from rastro.application.pagination import extract_paginated
from rastro.application.spider import extract_spider
from rastro.application.spider_links import LinkHarvest
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import ExtractionResult, Limits, Recipe, Representation, Source
from rastro.domain.spider import SpiderPolicy
from rastro.ports.acquisition import AcquisitionPort
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
    scope = PAGINATION_SCOPE.get()
    remaining = scope.remaining if scope else 0
    started, started_at = perf_counter(), datetime.now(UTC)
    fetched = await acquisition.acquire(
        page,
        max_bytes=limits.max_input_bytes(recipe.representation),
        representation=recipe.representation,
    )
    if scope is not None:  # an acquirer that does not charge the scope is still counted
        scope.remaining = min(scope.remaining, remaining - fetched.requests)
    result = await extract_page(fetched)
    return stamp_acquired(
        result, source=page, fetched=fetched, started=started, started_at=started_at
    )


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
) -> ExtractionResult[T]:
    """Spider ``source``: every page is fetched by ``fetch_spider_page`` and harvested."""

    def fetch(page: Source, harvest: LinkHarvest) -> Awaitable[ExtractionResult[T]]:
        async def extract_page(fetched: AcquisitionResult) -> ExtractionResult[T]:
            result = await extract_fetched(fetched, page)
            harvest.add(fetched.url, fetched.body, result, representation)
            return result

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

    return await extract_spider(
        source=source,
        schema=schema,
        spider=spider,
        policy=policy,
        parser=parser,
        limits=limits,
        sequential=recipe.pagination is not None,
        fetch_page=fetch,
    )
