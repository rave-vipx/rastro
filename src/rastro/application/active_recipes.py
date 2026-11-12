"""Choose, per job source, between the job file's recipe and the serving one in memory.

A serving recipe in ``recipe_memory`` wins only when its version is higher than the
job file's recipe version, so a promoted repair is used without editing the job file,
and a hand-edited recipe with a bumped version is used again (ADR 0021).
"""

from __future__ import annotations

from pathlib import Path

from rastro.application.job_loader import validate_job_paths
from rastro.domain.job_results import RecipeSelection
from rastro.domain.jobs import FileJobSource, Job, JobSource
from rastro.domain.memory import RecipeKey
from rastro.ports.recipes import RecipeRepository


def source_key(job: Job, item: JobSource) -> RecipeKey:
    """The recipe-memory key the engine uses for one job source."""
    url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
    source = (item.region or job.region).to_source(url)
    return RecipeKey.for_source(
        source, job.build_schema(), job.context, representation=item.representation
    )


def select_recipes(
    job: Job, repository: RecipeRepository | None
) -> tuple[Job, tuple[RecipeSelection, ...]]:
    """Return the job to execute and the recipe each source will use."""
    if job.recipe_version is not None:
        return job, tuple(
            RecipeSelection(
                source_index=i, recipe_origin="memory", recipe_version=job.recipe_version
            )
            for i in range(1, len(job.sources) + 1)
        )
    sources: list[JobSource] = []
    selections: list[RecipeSelection] = []
    for index, item in enumerate(job.sources, start=1):
        declared = item.recipe or job.recipe
        serving = repository.get(source_key(job, item)) if repository is not None else None
        stored = serving.recipe.version if serving is not None else None
        if (declared is None and job.recipe_memory is not None) or (
            declared is not None and stored is not None and stored > declared.version
        ):
            sources.append(item.model_copy(update={"recipe": None}))
            selections.append(
                RecipeSelection(source_index=index, recipe_origin="memory", recipe_version=stored)
            )
        else:
            sources.append(item.model_copy(update={"recipe": declared}))
            selections.append(
                RecipeSelection(
                    source_index=index,
                    recipe_origin="job",
                    recipe_version=declared.version if declared is not None else None,
                )
            )
    if all(s.recipe_origin == "job" for s in selections):
        return job, tuple(selections)  # unchanged, so batched URL extraction still applies
    return job.model_copy(update={"sources": tuple(sources), "recipe": None}), tuple(selections)


def active_job(
    job: Job, *, base_dir: Path | None, allow_absolute_paths: bool
) -> tuple[Job, tuple[RecipeSelection, ...]]:
    """Open ``recipe_memory`` read-only when it exists and select each source's recipe."""
    _, memory_path = validate_job_paths(
        job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    repository = None
    if memory_path is not None and memory_path.is_file():
        from rastro.adapters.sqlite import SQLiteRecipeRepository

        repository = SQLiteRecipeRepository(memory_path)
    return select_recipes(job, repository)
