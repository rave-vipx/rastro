"""Prepared extraction for supplied and acquired documents behind the Rastro facade."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter

import structlog
from pydantic import BaseModel

from rastro.application.batch import run_batch
from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import PreparedRecipe, RecipeMemory
from rastro.application.outcomes import failure_result, stamp_acquired, stamp_provided
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.memory import RecipeContext
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    Representation,
    ResourceLimitError,
    Source,
)
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
    ) -> None:
        self._parsers = dict(parsers)
        self._memory = memory
        self._learner = learner
        self._limits = limits
        self._discover_recipes = discover_recipes
        self._acquisition = acquisition
        self._policy = policy

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
        prepared = await self._prepare(
            source, schema, recipe, context, recipe_version, representation
        )
        result = await self._extract_prepared(text, source, schema, prepared, representation)
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
        prepared = await self._prepare(
            source, schema, recipe, context, recipe_version, representation
        )
        fetched = await self._acquisition.acquire(
            source,
            max_bytes=self._limits.max_input_bytes(representation),
            representation=representation,
        )
        result = await self._extract_fetched(
            fetched, source, schema, prepared, representation, started_at
        )
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

    async def _extract_fetched[T: BaseModel](
        self,
        fetched: AcquisitionResult,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        representation: Representation,
        started_at: datetime,
    ) -> ExtractionResult[T]:
        final_source = Source.model_validate({**source.model_dump(), "url": fetched.url})
        issue = fetched.issue_code
        if fetched.body is not None and fetched.status == Outcome.SUCCESS:
            try:
                return await self._extract_prepared(
                    fetched.body, final_source, schema, prepared, representation
                )
            except ResourceLimitError:
                issue = "extraction_limit"
        status = Outcome.PERMANENT_FAILURE if fetched.status == Outcome.SUCCESS else fetched.status
        return failure_result(
            status=status,
            issue_code=issue or "invalid_acquisition_result",
            source=source,
            schema=schema,
            started_at=started_at,
            source_url=final_source.evidence_url,
            storage_seconds=prepared.storage_seconds,
        )

    async def _prepare(
        self,
        source: Source,
        schema: type[BaseModel],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> PreparedRecipe:
        return await asyncio.to_thread(
            self._memory.prepare,
            source,
            schema,
            recipe,
            context,
            recipe_version,
            allow_discovery=self._discover_recipes,
            representation=representation,
        )

    async def _extract_prepared[T: BaseModel](
        self,
        text: str,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        representation: Representation,
    ) -> ExtractionResult[T]:
        """Learn a recipe for a cold context; otherwise apply the prepared definition."""
        parser = self._parsers[representation]
        if prepared.definition is None:
            return await self._learner.extract(
                text,
                source=source,
                schema=schema,
                prepared=prepared,
                parser=parser,
                limits=self._limits,
                representation=representation,
            )
        return await asyncio.to_thread(
            self._memory.extract,
            text,
            source=source,
            schema=schema,
            prepared=prepared,
            parser=parser,
            limits=self._limits,
        )
