"""Repair evidence composition at the CLI edge."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rastro.application.success_samples import SuccessfulSample
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import Job
from rastro.domain.models import Issue, Outcome
from rastro.repair_evidence import retain_sample


def retain_successes(
    path: Path,
    dataset: str,
    schema: type[BaseModel],
    report: JobRunResult[Any],
    samples: dict[str, SuccessfulSample],
) -> dict[str, tuple[Issue, ...]]:
    issues: dict[str, tuple[Issue, ...]] = {}
    for result in report.results:
        sample = samples.get(result.job_id)
        if sample is None or result.status != Outcome.SUCCESS:
            continue
        retained = retain_sample(path, dataset, schema, sample)
        if retained:
            issues[result.source_url] = retained
    return issues


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
