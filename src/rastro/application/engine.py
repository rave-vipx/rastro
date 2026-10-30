"""The public facade: wires adapters and delegates to the extraction service."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel

from rastro.application.extraction import validate_recipe
from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import RecipeMemory
from rastro.application.service import ExtractionService
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.memory import RecipeContext
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Recipe,
    Representation,
    ResourceLimitError,
    Source,
)
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.discovery import RecipeDiscovery
from rastro.ports.model import ModelPort
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeRepository


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
        memory = RecipeMemory(repository)
        if discovery is None:
            from rastro.adapters.discovery import ScraplingDiscovery

            discovery = ScraplingDiscovery()
        inference_policy = InferencePolicy.model_validate(
            (inference_policy or InferencePolicy()).model_dump()
        )
        learner = RecipeLearner(
            model=model,
            discovery=discovery,
            policy=inference_policy,
            repository=repository,
            json_discovery=JsonDiscovery(),
        )
        if acquisition is not None and policy is None:
            raise ConfigurationError("An acquisition port requires a batch policy")
        self._acquisition = acquisition
        self._policy = policy
        self._service = ExtractionService(
            parsers={"html": parser, "json": JsonParser()},
            memory=memory,
            learner=learner,
            limits=limits or Limits(),
            discover_recipes=discover_recipes or model is not None,
            acquisition=acquisition,
        )

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
        return self._service.extract_provided(
            html,
            source=source,
            schema=schema,
            recipe=recipe,
            context=context,
            recipe_version=recipe_version,
            representation="html",
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
        return self._service.extract_provided(
            text,
            source=source,
            schema=schema,
            recipe=recipe,
            context=context,
            recipe_version=recipe_version,
            representation="json",
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
        return await self._service.extract_provided_async(
            html,
            source=source,
            schema=schema,
            recipe=recipe,
            context=context,
            recipe_version=recipe_version,
            representation="html",
        )

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
        return await self._service.extract_provided_async(
            text,
            source=source,
            schema=schema,
            recipe=recipe,
            context=context,
            recipe_version=recipe_version,
            representation="json",
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
        return await self._service.extract_acquired(
            source=source,
            schema=schema,
            recipe=recipe,
            context=context,
            recipe_version=recipe_version,
            representation=representation,
        )

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
