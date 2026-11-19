"""Prepared extraction for supplied and acquired documents behind the Rastro facade."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from time import perf_counter

import structlog
from pydantic import BaseModel

from rastro.application.batch import run_batch
from rastro.application.browser_sessions import source_session
from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import PreparedRecipe, RecipeMemory
from rastro.application.outcomes import failure_result, stamp_acquired, stamp_provided
from rastro.application.page_steps import recipe_steps, stepped
from rastro.application.pagination import extract_paginated
from rastro.application.prepared_extraction import PreparedExtraction
from rastro.application.scroll import scroll_scope, scrolled
from rastro.application.spider_page import crawl_source
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.memory import RecipeContext
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    Representation,
    Source,
)
from rastro.domain.scroll import ScrollSpec
from rastro.domain.spider import SpiderPolicy
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.parser import DocumentParser

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


class ExtractionService:
    """Select a parser and recipe path per representation; stamp acquisition metrics."""

    def __init__(
        self,
        *,
        parsers: Mapping[Representation, DocumentParser],
        memory: RecipeMemory,
        learner: RecipeLearner,
        limits: Limits,
        discover_recipes: bool,
        acquisition: AcquisitionPort | None,
        policy: AcquisitionPolicy | None = None,
        scroll: ScrollSpec | None = None,
        spider: SpiderPolicy | None = None,
    ) -> None:
        self._prepared = PreparedExtraction(
            parsers=parsers,
            memory=memory,
            learner=learner,
            limits=limits,
            discover_recipes=discover_recipes,
        )
        self._parsers = self._prepared.parsers
        self._memory = memory
        self._limits = limits
        self._acquisition = acquisition
        self._scroll = scroll
        self._policy = policy
        self._spider = spider if spider is not None and spider.mode == "spider" else None

    def extract_provided[T: BaseModel](
        self,
        text: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> ExtractionResult[T]:
        """Extract an explicit or remembered recipe synchronously; never discovers."""
        prepared = self._memory.prepare(
            source, schema, recipe, context, recipe_version, representation=representation
        )
        result = self._memory.extract(
            text,
            source=source,
            schema=schema,
            prepared=prepared,
            parser=self._parsers[representation],
            limits=self._limits,
        )
        return stamp_provided(result, representation)

    async def extract_provided_async[T: BaseModel](
        self,
        text: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> ExtractionResult[T]:
        prepared = await self._prepared.prepare(
            source, schema, recipe, context, recipe_version, representation
        )
        result = await self._prepared.extract_text(text, source, schema, prepared, representation)
        return stamp_provided(result, representation)

    async def extract_acquired[T: BaseModel](
        self,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> ExtractionResult[T]:
        """Acquire one permitted source, then validate it through the offline engine."""
        if self._acquisition is None:
            raise ConfigurationError("Use Rastro.web(policy) or supply an acquisition port")
        started, started_at = perf_counter(), datetime.now(UTC)
        prepared = await self._prepared.prepare(
            source, schema, recipe, context, recipe_version, representation
        )
        if self._spider is not None:
            return await self._extract_spider(source, schema, prepared, representation, started_at)

        async def extract_one(fetched: AcquisitionResult) -> ExtractionResult[T]:
            return await self._prepared.extract_fetched(
                fetched, source, schema, prepared, representation, started_at
            )

        steps = recipe_steps(prepared.definition)  # run on the first page, before scroll
        extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]] = stepped(
            extract_one, steps=steps, acquisition=self._acquisition, limits=self._limits
        )

        paginated = prepared.definition is not None and prepared.definition.pagination is not None
        if self._scroll is not None:
            extract_page = scrolled(
                extract_page,
                spec=self._scroll,
                acquisition=self._acquisition,
                limits=self._limits,
                owns_scope=not paginated,
            )
        async with source_session():  # one browser context for this source, closed after it
            if paginated:
                assert prepared.definition is not None
                return await extract_paginated(
                    source=source,
                    schema=schema,
                    recipe=prepared.definition,
                    parser=self._parsers[representation],
                    limits=self._limits,
                    acquisition=self._acquisition,
                    policy=self._policy,
                    extract_page=extract_page,
                )
            with scroll_scope(source, self._policy) if self._scroll or steps else nullcontext():
                fetched = await self._acquisition.acquire(
                    source,
                    max_bytes=self._limits.max_input_bytes(representation),
                    representation=representation,
                )
                result = await extract_page(fetched)
        stamped = stamp_acquired(
            result, source=source, fetched=fetched, started=started, started_at=started_at
        )
        _LOG.info(
            "acquisition_completed",
            job_id=stamped.job_id,
            status=stamped.status.value,
            request_count=fetched.requests,
            retry_count=fetched.retries,
            egress=fetched.egress,
            acquisition_seconds=fetched.acquisition_seconds,
        )
        return stamped

    async def _extract_spider[T: BaseModel](
        self,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        representation: Representation,
        started_at: datetime,
    ) -> ExtractionResult[T]:
        recipe, spider, acquisition = prepared.definition, self._spider, self._acquisition
        if recipe is None or spider is None or acquisition is None:
            return failure_result(  # a learned recipe is not reused page to page yet
                status=Outcome.PERMANENT_FAILURE,
                issue_code="spider_needs_recipe",
                source=source,
                schema=schema,
                started_at=started_at,
            )
        return await crawl_source(
            source,
            schema=schema,
            spider=spider,
            recipe=recipe,
            parser=self._parsers[representation],
            limits=self._limits,
            acquisition=acquisition,
            policy=self._policy,
            representation=representation,
            extract_fetched=lambda fetched, page: self._prepared.extract_fetched(
                fetched, page, schema, prepared, representation, started_at
            ),
            scroll=self._scroll,
        )

    async def extract_many[T: BaseModel](
        self,
        sources: Sequence[Source],
        *,
        schema: type[T],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> tuple[ExtractionResult[T], ...]:
        """Preserve input order with a bounded worker pool; cancellation cancels all jobs."""
        return await run_batch(
            sources,
            schema=schema,
            recipe=recipe,
            representation=representation,
            policy=self._policy,
            has_acquisition=self._acquisition is not None,
            extract_one=lambda source, validated: self.extract_acquired(
                source=source,
                schema=schema,
                recipe=validated,
                context=context,
                recipe_version=recipe_version,
                representation=representation,
            ),
        )
