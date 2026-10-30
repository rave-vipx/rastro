"""Schema validation and evidence shared by supplied HTML and asynchronous acquisition."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

import structlog
from pydantic import BaseModel

from rastro.application.extraction import validate_recipe
from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import PreparedRecipe, RecipeMemory
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.intelligence import InferencePolicy
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
from rastro.ports.discovery import RecipeDiscovery
from rastro.ports.model import ModelPort
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeRepository

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


class Rastro:
    """Extract supplied HTML or JSON, or configure permission-aware HTTP acquisition."""

    def __init__(
        self,
        *,
        parser: DocumentParser | None = None,
        limits: Limits | None = None,
        acquisition: AcquisitionPort | None = None,
        policy: AcquisitionPolicy | None = None,
        repository: RecipeRepository | None = None,
        model: ModelPort | None = None,
        inference_policy: InferencePolicy | None = None,
        discover_recipes: bool = False,
        discovery: RecipeDiscovery | None = None,
    ) -> None:
        from rastro.adapters.structured import JsonDiscovery, JsonParser

        if parser is None:
            from rastro.adapters.scrapling import ScraplingParser

            parser = ScraplingParser()
        self._json_parser = JsonParser()
        self._memory = RecipeMemory(repository)
        if discovery is None:
            from rastro.adapters.discovery import ScraplingDiscovery

            discovery = ScraplingDiscovery()
        inference_policy = InferencePolicy.model_validate(
            (inference_policy or InferencePolicy()).model_dump()
        )
        self._discover_recipes = discover_recipes or model is not None
        self._learner = RecipeLearner(
            model=model,
            discovery=discovery,
            policy=inference_policy,
            repository=repository,
            json_discovery=JsonDiscovery(),
        )
        self._parser = parser
        self._limits = limits or Limits()
        self._acquisition = acquisition
        self._policy = policy
        if acquisition is not None and policy is None:
            raise ConfigurationError("An acquisition port requires a batch policy")

    def extract_html[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
    ) -> ExtractionResult[T]:
        """Extract explicit recipes, or reuse validated memory for the exact context."""
        prepared = self._memory.prepare(source, schema, recipe, context, recipe_version)
        return self._memory.extract(
            html,
            source=source,
            schema=schema,
            prepared=prepared,
            parser=self._parser,
            limits=self._limits,
        )

    def extract_json[T: BaseModel](
        self,
        text: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
    ) -> ExtractionResult[T]:
        """Extract supplied JSON text with a `json` recipe; does no fetching."""
        prepared = self._memory.prepare(
            source, schema, recipe, context, recipe_version, representation="json"
        )
        return _provided_json(
            self._memory.extract(
                text,
                source=source,
                schema=schema,
                prepared=prepared,
                parser=self._json_parser,
                limits=self._limits,
            )
        )

    @classmethod
    def web(
        cls,
        policy: AcquisitionPolicy,
        *,
        limits: Limits | None = None,
        repository: RecipeRepository | None = None,
        model: ModelPort | None = None,
        inference_policy: InferencePolicy | None = None,
        discover_recipes: bool = False,
    ) -> Rastro:
        """Create an HTTP client. Install rastro-engine[http]; use one event loop per client."""
        try:
            from rastro.adapters.dns import SystemResolver
            from rastro.adapters.http import CurlTransport
            from rastro.application.acquisition import WebAcquirer
        except ModuleNotFoundError as error:
            if error.name not in {"curl_cffi", "protego"}:
                raise
            raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None
        return cls(
            acquisition=WebAcquirer(policy, transport=CurlTransport(), resolver=SystemResolver()),
            policy=policy,
            limits=limits,
            repository=repository,
            model=model,
            inference_policy=inference_policy,
            discover_recipes=discover_recipes,
        )

    async def extract_html_async[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
    ) -> ExtractionResult[T]:
        """Run offline extraction, with optional cold-context discovery and inference."""
        prepared = await asyncio.to_thread(
            self._memory.prepare,
            source,
            schema,
            recipe,
            context,
            recipe_version,
            allow_discovery=self._discover_recipes,
        )
        return await self._extract_prepared(html, source, schema, prepared)

    async def extract_json_async[T: BaseModel](
        self,
        text: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
    ) -> ExtractionResult[T]:
        """Extract supplied JSON, with optional cold-context structured discovery."""
        prepared = await asyncio.to_thread(
            self._memory.prepare,
            source,
            schema,
            recipe,
            context,
            recipe_version,
            allow_discovery=self._discover_recipes,
            representation="json",
        )
        result = await self._extract_prepared(text, source, schema, prepared, "json")
        return _provided_json(result)

    async def _extract_prepared[T: BaseModel](
        self,
        text: str,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        representation: Representation = "html",
    ) -> ExtractionResult[T]:
        parser = self._json_parser if representation == "json" else self._parser
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

    async def extract[T: BaseModel](
        self,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
        representation: Representation = "html",
    ) -> ExtractionResult[T]:
        """Acquire one permitted source, then validate it through the offline engine.

        `representation="json"` requests `application/json` and requires a JSON response.
        """
        if self._acquisition is None:
            raise ConfigurationError("Use Rastro.web(policy) or supply an acquisition port")
        started, started_at = perf_counter(), datetime.now(UTC)
        prepared = await asyncio.to_thread(
            self._memory.prepare,
            source,
            schema,
            recipe,
            context,
            recipe_version,
            allow_discovery=self._discover_recipes,
            representation=representation,
        )
        max_bytes = self._limits.max_input_bytes(representation)
        if representation == "json":
            fetched = await self._acquisition.acquire(
                source, max_bytes=max_bytes, representation="json"
            )
        else:
            # HTML ports predating structured responses need not accept `representation`.
            fetched = await self._acquisition.acquire(source, max_bytes=max_bytes)
        final_source = Source.model_validate({**source.model_dump(), "url": fetched.url})
        issue = fetched.issue_code
        result: ExtractionResult[T] | None = None
        if fetched.html is not None and fetched.status == Outcome.SUCCESS:
            try:
                result = await self._extract_prepared(
                    fetched.html, final_source, schema, prepared, representation
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

    async def extract_many[T: BaseModel](
        self,
        sources: Sequence[Source],
        *,
        schema: type[T],
        recipe: Recipe | None = None,
        context: RecipeContext | None = None,
        recipe_version: int | None = None,
        representation: Representation = "html",
    ) -> tuple[ExtractionResult[T], ...]:
        """Preserve input order with a bounded worker pool; cancellation cancels all jobs."""
        if self._acquisition is None or self._policy is None:
            raise ConfigurationError("Batch acquisition requires Rastro.web(policy)")
        if recipe is not None:
            recipe = validate_recipe(schema, recipe, representation)
        if len(sources) > self._policy.max_sources:
            raise ResourceLimitError("Batch exceeds max_sources")
        pending = iter(enumerate(tuple(sources)))
        results: list[ExtractionResult[T] | None] = [None] * len(sources)

        async def worker() -> None:
            for index, source in pending:
                results[index] = await self.extract(
                    source=source,
                    schema=schema,
                    recipe=recipe,
                    context=context,
                    recipe_version=recipe_version,
                    representation=representation,
                )

        async with asyncio.TaskGroup() as group:
            for _ in range(min(len(sources), self._policy.max_concurrency)):
                group.create_task(worker())
        return tuple(result for result in results if result is not None)


def _provided_json[T: BaseModel](result: ExtractionResult[T]) -> ExtractionResult[T]:
    metrics = result.metrics.model_copy(update={"acquisition_method": "provided_json"})
    return result.model_copy(update={"metrics": metrics})
