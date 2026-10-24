"""Select exact-context recipes and remember only successfully validated alternatives."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

import structlog
from pydantic import BaseModel

from rastro.application.extraction import extract_document, validate_recipe
from rastro.domain.memory import (
    RecipeContext,
    RecipeKey,
    RecipeNotFoundError,
    RecipeStorageError,
)
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    RecipeAttempt,
    Source,
)
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeRepository

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


@dataclass(frozen=True)
class PreparedRecipe:
    definition: Recipe
    key: RecipeKey | None
    selected_index: int | None
    storage_seconds: float
    started: float
    started_at: datetime


class RecipeMemory:
    def __init__(self, repository: RecipeRepository | None) -> None:
        self._repository = repository

    def prepare(
        self,
        source: Source,
        schema: type[BaseModel],
        recipe: Recipe | None,
        context: RecipeContext | None,
        recipe_version: int | None,
    ) -> PreparedRecipe:
        started, started_at = perf_counter(), datetime.now(UTC)
        if recipe is not None and recipe_version is not None:
            raise ConfigurationError("recipe_version selects stored recipes; omit recipe")
        if self._repository is None:
            if recipe is None or context is not None or recipe_version is not None:
                raise ConfigurationError("Recipe reuse and context require a repository")
            return PreparedRecipe(
                validate_recipe(schema, recipe), None, None, 0, started, started_at
            )
        key = RecipeKey.for_source(source, schema, context)
        if recipe is not None:
            return PreparedRecipe(
                validate_recipe(schema, recipe), key, None, 0, started, started_at
            )
        storage_start = perf_counter()
        stored = self._repository.get(key, version=recipe_version)
        storage_seconds = perf_counter() - storage_start
        if stored is None:
            raise RecipeNotFoundError("No validated recipe for this context and version")
        if stored.key != key or (
            recipe_version is not None and stored.recipe.version != recipe_version
        ):
            raise RecipeStorageError("Repository returned an incompatible recipe")
        return PreparedRecipe(
            validate_recipe(schema, stored.recipe),
            key,
            stored.selected_index,
            storage_seconds,
            started,
            started_at,
        )

    def extract[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        parser: DocumentParser,
        limits: Limits,
    ) -> ExtractionResult[T]:
        definition = prepared.definition
        candidates = (definition, *definition.fallbacks)
        indexes = (
            range(len(candidates))
            if prepared.selected_index is None
            else (prepared.selected_index,)
        )
        attempts: list[RecipeAttempt] = []
        results: list[ExtractionResult[T]] = []
        for index in indexes:
            result = extract_document(
                html,
                source=source,
                schema=schema,
                recipe=candidates[index],
                parser=parser,
                limits=limits,
            )
            results.append(result)
            attempts.append(RecipeAttempt(index=index, status=result.status))
            # A partial dataset is explicit evidence; do not hide it with another strategy.
            if result.records:
                break
        result = results[-1]
        storage_seconds = prepared.storage_seconds
        if result.status == Outcome.SUCCESS and self._repository is not None:
            assert prepared.key is not None
            storage_start = perf_counter()
            self._repository.save_success(
                prepared.key, definition, selected_index=attempts[-1].index, at=result.completed_at
            )
            storage_seconds += perf_counter() - storage_start
        metrics = result.metrics.model_copy(
            update={
                "storage_seconds": storage_seconds,
                "extraction_seconds": sum(item.metrics.extraction_seconds for item in results),
                "validation_seconds": sum(item.metrics.validation_seconds for item in results),
                "candidates": sum(item.metrics.candidates for item in results),
                "rejected": sum(item.metrics.rejected for item in results),
                "total_seconds": perf_counter() - prepared.started,
            }
        )
        result = result.model_copy(
            update={
                "recipe_version": definition.version,
                "recipe_reused": prepared.selected_index is not None,
                "recipe_attempts": tuple(attempts),
                "started_at": prepared.started_at,
                "completed_at": datetime.now(UTC),
                "metrics": metrics,
            }
        )
        _LOG.info(
            "extraction_completed",
            job_id=result.job_id,
            status=result.status.value,
            accepted=len(result.records),
            rejected=metrics.rejected,
            recipe_version=definition.version,
            recipe_reused=result.recipe_reused,
            recipe_attempts=len(attempts),
            storage_seconds=storage_seconds,
            total_seconds=metrics.total_seconds,
        )
        return result
