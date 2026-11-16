"""Recipe preparation and extraction of fetched documents (one collaborator of the service)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel

from rastro.application.intelligence import RecipeLearner
from rastro.application.memory import PreparedRecipe, RecipeMemory
from rastro.application.outcomes import failure_result
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.memory import RecipeContext
from rastro.domain.models import (
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    Representation,
    ResourceLimitError,
    Source,
)
from rastro.ports.parser import DocumentParser


class PreparedExtraction:
    """Prepare a recipe for a source, then apply it to text or to an acquisition result."""

    def __init__(
        self,
        *,
        parsers: Mapping[Representation, DocumentParser],
        memory: RecipeMemory,
        learner: RecipeLearner,
        limits: Limits,
        discover_recipes: bool,
    ) -> None:
        self.parsers = dict(parsers)
        self.memory = memory
        self._learner = learner
        self._limits = limits
        self._discover_recipes = discover_recipes

    async def prepare(
        self,
        source: Source,
        schema: type[BaseModel],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
        representation: Representation,
    ) -> PreparedRecipe:
        return await asyncio.to_thread(
            self.memory.prepare,
            source,
            schema,
            recipe,
            context,
            recipe_version,
            allow_discovery=self._discover_recipes,
            representation=representation,
        )

    async def extract_text[T: BaseModel](
        self,
        text: str,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        representation: Representation,
    ) -> ExtractionResult[T]:
        """Learn a recipe for a cold context; otherwise apply the prepared definition."""
        parser = self.parsers[representation]
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
            self.memory.extract,
            text,
            source=source,
            schema=schema,
            prepared=prepared,
            parser=parser,
            limits=self._limits,
        )

    async def extract_fetched[T: BaseModel](
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
                return await self.extract_text(
                    fetched.body, final_source, schema, prepared, representation
                )
            except ResourceLimitError:
                issue = "extraction_limit"
        if fetched.status == Outcome.UNCHANGED:
            return failure_result(
                status=Outcome.UNCHANGED,
                issue_code="not_modified",
                source=source,
                schema=schema,
                started_at=started_at,
                source_url=final_source.evidence_url,
                storage_seconds=prepared.storage_seconds,
            ).model_copy(update={"issues": ()})
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
