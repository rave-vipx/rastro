"""Bounded ordered batch execution over an async single-source extractor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from rastro.application.extraction import validate_recipe
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Recipe,
    Representation,
    ResourceLimitError,
    Source,
)


async def run_batch[T: BaseModel](
    sources: Sequence[Source],
    *,
    schema: type[T],
    recipe: Recipe | None,
    representation: Representation,
    policy: AcquisitionPolicy | None,
    has_acquisition: bool,
    extract_one: Callable[[Source, Recipe | None], Awaitable[ExtractionResult[T]]],
) -> tuple[ExtractionResult[T], ...]:
    """Preserve input order with a bounded worker pool; cancellation cancels all jobs."""
    if not has_acquisition or policy is None:
        raise ConfigurationError("Batch acquisition requires Rastro.web(policy)")
    validated = validate_recipe(schema, recipe, representation) if recipe is not None else None
    if len(sources) > policy.max_sources:
        raise ResourceLimitError("Batch exceeds max_sources")
    pending = iter(enumerate(tuple(sources)))
    results: list[ExtractionResult[T] | None] = [None] * len(sources)

    async def worker() -> None:
        for index, source in pending:
            results[index] = await extract_one(source, validated)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(len(sources), policy.max_concurrency)):
            group.create_task(worker())
    return tuple(result for result in results if result is not None)
