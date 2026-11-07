"""Repair evidence composition at the CLI edge."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.success_samples import SuccessfulSample
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import Job
from rastro.domain.models import CssRecipe, Outcome, Source
from rastro.mocklab.fixtures import build_fixture


def retain_successes(
    path: Path,
    dataset: str,
    schema: type[BaseModel],
    report: JobRunResult[Any],
    samples: dict[str, SuccessfulSample],
) -> None:
    for result in report.results:
        sample = samples.get(result.job_id)
        if sample is None or result.status != Outcome.SUCCESS:
            continue
        fixture_source = Source.model_validate(
            {**sample.source.model_dump(), "url": "https://fixture.invalid/"}
        )
        fixture_result = sample.result.model_copy(
            update={
                "source_url": fixture_source.evidence_url,
                "records": tuple(
                    record.model_copy(
                        update={
                            "evidence": {
                                name: evidence.model_copy(
                                    update={"source_url": fixture_source.evidence_url}
                                )
                                for name, evidence in record.evidence.items()
                            }
                        }
                    )
                    for record in sample.result.records
                ),
            }
        )
        fixture = build_fixture(
            sample.body,
            source=fixture_source,
            schema=schema,
            recipe=sample.recipe,
            result=fixture_result,
            limits=sample.limits,
            synthetic_count=0,
        )
        snapshot = None
        if isinstance(sample.recipe, CssRecipe):
            snapshot = ScraplingRelocation().capture(
                sample.body,
                source=sample.source,
                recipe=sample.recipe,
                result=sample.result,
                limits=sample.limits,
            )
        SQLiteRelocationStore(path, limits=sample.limits).save(
            dataset, sample.source, fixture, snapshot, observed_at=sample.result.completed_at
        )


def serving_job(job: Job, base: Path, allow: bool) -> Job:
    """Dataset loads use the lifecycle's serving recipe after the first successful load."""
    if job.recipe_memory is None or job.recipe_version is not None:
        return job
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.cli_repair_context import safe_path
    from rastro.domain.jobs import FileJobSource
    from rastro.domain.memory import RecipeKey

    path = safe_path(job.recipe_memory, base, allow)
    if not path.exists():
        return job
    repository = SQLiteRecipeRepository(path)
    schema = job.build_schema()
    sources = []
    for item in job.sources:
        url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
        source = (item.region or job.region).to_source(url)
        key = RecipeKey.for_source(source, schema, job.context, representation=item.representation)
        serving = repository.get(key)
        sources.append(
            item.model_copy(update={"recipe": None if serving else item.recipe or job.recipe})
        )
    return job.model_copy(update={"sources": tuple(sources), "recipe": None})
