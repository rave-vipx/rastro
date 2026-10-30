"""Prepared extraction for supplied and acquired documents behind the Rastro facade."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel

from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import PreparedRecipe, RecipeMemory
from rastro.domain.memory import RecipeContext
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Issue,
    Limits,
    Metrics,
    Outcome,
    Recipe,
    Representation,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.parser import DocumentParser

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_PROVIDED: dict[Representation, Literal["provided_html", "provided_json"]] = {
    "html": "provided_html",
    "json": "provided_json",
}


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
    ) -> None:
        self._parsers = dict(parsers)
        self._memory = memory
        self._learner = learner
        self._limits = limits
        self._discover_recipes = discover_recipes
        self._acquisition = acquisition

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
        return _stamp_provided(result, representation)

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
        return _stamp_provided(result, representation)

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
        final_source = Source.model_validate({**source.model_dump(), "url": fetched.url})
        issue = fetched.issue_code
        result: ExtractionResult[T] | None = None
        if fetched.body is not None and fetched.status == Outcome.SUCCESS:
            try:
                result = await self._extract_prepared(
                    fetched.body, final_source, schema, prepared, representation
                )
            except ResourceLimitError:
                issue = "extraction_limit"
        if result is None:
            result = ExtractionResult[T](
                job_id=str(uuid4()),
                status=(
                    Outcome.PERMANENT_FAILURE
                    if fetched.status == Outcome.SUCCESS
                    else fetched.status
                ),
                source_url=final_source.evidence_url,
                locale=source.locale,
                timezone=source.timezone,
                schema_fingerprint=schema_fingerprint(schema),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                records=(),
                issues=(Issue(code=issue or "invalid_acquisition_result"),),
                metrics=Metrics(
                    egress=source.egress,
                    storage_seconds=prepared.storage_seconds,
                    input_bytes=0,
                    candidates=0,
                    accepted=0,
                    rejected=0,
                    extraction_seconds=0,
                    validation_seconds=0,
                    total_seconds=0,
                ),
            )
        metrics = result.metrics.model_copy(
            update={
                "acquisition_method": "http",
                "egress": fetched.egress,
                "acquisition_seconds": fetched.acquisition_seconds,
                "network_seconds": fetched.network_seconds,
                "request_count": fetched.requests,
                "retry_count": fetched.retries,
                "redirect_count": fetched.redirects,
                "bytes_received": fetched.bytes_received,
                "total_seconds": perf_counter() - started,
            }
        )
        result = result.model_copy(
            update={
                "started_at": started_at,
                "completed_at": datetime.now(UTC),
                "metrics": metrics,
                "requested_url": source.evidence_url,
                "http_status": fetched.http_status,
                "retry_after_seconds": fetched.retry_after_seconds,
            }
        )
        _LOG.info(
            "acquisition_completed",
            job_id=result.job_id,
            status=result.status.value,
            request_count=fetched.requests,
            retry_count=fetched.retries,
            egress=fetched.egress,
            acquisition_seconds=fetched.acquisition_seconds,
        )
        return result

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


def _stamp_provided[T: BaseModel](
    result: ExtractionResult[T], representation: Representation
) -> ExtractionResult[T]:
    metrics = result.metrics.model_copy(update={"acquisition_method": _PROVIDED[representation]})
    return result.model_copy(update={"metrics": metrics})
