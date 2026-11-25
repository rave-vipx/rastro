"""Run the sources of an activated job: extraction, per-source results and page dedupe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.application.job_loader import PreparedJob, prepare_job
from rastro.application.pagination_records import deduplicate_pages
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import FileJobSource, Job, UrlJobSource
from rastro.domain.models import ExtractionResult

if TYPE_CHECKING:
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort


async def extract_one_async(
    job: Job, prepared: PreparedJob, index: int, item: FileJobSource | UrlJobSource
) -> ExtractionResult[Any]:
    recipe = item.recipe or job.recipe
    if isinstance(item, FileJobSource):
        src = (item.region or job.region).to_source(item.resolved_source_url())
        extractor = (
            prepared.client.extract_json_async
            if item.representation == "json"
            else prepared.client.extract_html_async
        )
        return await extractor(
            prepared.bodies[index],
            source=src,
            schema=prepared.schema,
            recipe=recipe,
            context=job.context,
            recipe_version=job.recipe_version,
        )
    return await prepared.client.extract(
        source=(item.region or job.region).to_source(item.url),
        schema=prepared.schema,
        recipe=recipe,
        context=job.context,
        recipe_version=job.recipe_version,
        representation=item.representation,
    )


async def run_active_async(
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
) -> JobRunResult[Any]:
    prepared = prepare_job(
        job,
        base_dir=base_dir,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=acquisition,
        model=model,
    )
    if (
        all(isinstance(item, UrlJobSource) and item.recipe is None for item in job.sources)
        and len({item.representation for item in job.sources}) == 1
    ):
        batch = [
            (item.region or job.region).to_source(item.url)
            for item in job.sources
            if isinstance(item, UrlJobSource)
        ]
        results = await prepared.client.extract_many(
            batch,
            schema=prepared.schema,
            recipe=job.recipe,
            context=job.context,
            recipe_version=job.recipe_version,
            representation=job.sources[0].representation,
        )
        return JobRunResult.from_results(
            schema_fingerprint=prepared.fingerprint,
            results=[deduplicate_pages(result, job.dataset) for result in results],
        )
    collected = [
        await extract_one_async(job, prepared, idx, item) for idx, item in enumerate(job.sources)
    ]
    return JobRunResult.from_results(
        schema_fingerprint=prepared.fingerprint,
        results=[deduplicate_pages(result, job.dataset) for result in collected],
    )


def run_active(
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
) -> JobRunResult[Any]:
    if (
        any(isinstance(item, UrlJobSource) for item in job.sources)
        or job.discover_recipes
        or job.model is not None
        or model is not None
    ):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(
                run_active_async(
                    job,
                    base_dir=base_dir,
                    allow_absolute_paths=allow_absolute_paths,
                    acquisition=acquisition,
                    model=model,
                )
            )
    prepared = prepare_job(
        job,
        base_dir=base_dir,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=None,
        model=None,
    )
    collected: list[ExtractionResult[Any]] = []
    for idx, item in enumerate(job.sources):
        assert isinstance(item, FileJobSource)
        extractor = (
            prepared.client.extract_json
            if item.representation == "json"
            else prepared.client.extract_html
        )
        collected.append(
            extractor(
                prepared.bodies[idx],
                source=(item.region or job.region).to_source(item.resolved_source_url()),
                schema=prepared.schema,
                recipe=item.recipe or job.recipe,
                context=job.context,
                recipe_version=job.recipe_version,
            )
        )
    return JobRunResult.from_results(
        schema_fingerprint=prepared.fingerprint,
        results=[deduplicate_pages(result, job.dataset) for result in collected],
    )
