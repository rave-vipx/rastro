"""Choose, per job source, between the job file's recipe and the serving one in memory.

A serving recipe in ``recipe_memory`` wins only when its version is higher than the
job file's recipe version, so a promoted repair is used without editing the job file,
and a hand-edited recipe with a bumped version is used again (ADR 0021).
"""

from __future__ import annotations

from pathlib import Path

from rastro.application.job_loader import recipe_memory_path, validate_job_paths
from rastro.domain.job_results import RecipeSelection
from rastro.domain.jobs import FileJobSource, Job, JobSource
from rastro.domain.memory import RecipeKey, RecipeStorageError
from rastro.ports.recipes import RecipeRepository


def source_key(job: Job, item: JobSource) -> RecipeKey:
    """The recipe-memory key the engine uses for one job source."""
    url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
    source = (item.region or job.region).to_source(url)
    return RecipeKey.for_source(
        source, job.build_schema(), job.context, representation=item.representation
    )


def memory_wins(declared: int | None, stored: int | None) -> bool:
    """The one selection rule: memory wins without a job recipe, or with a higher version.

    ``stored`` is the serving version in memory (``None`` when nothing is stored yet, in
    which case memory still wins without a job recipe because the engine learns into it).
    """
    return declared is None or (stored is not None and stored > declared)


def select_recipes(
    job: Job, repository: RecipeRepository | None
) -> tuple[Job, tuple[RecipeSelection, ...]]:
    """Return the job to execute and the recipe each source will use."""
    if job.recipe_version is not None:
        # Report the version the store really holds; None when it is missing (the run
        # then fails with RecipeNotFoundError for that source).
        pinned = [
            repository.get(source_key(job, item), version=job.recipe_version)
            if repository is not None
            else None
            for item in job.sources
        ]
        return job, tuple(
            RecipeSelection(
                source_index=i,
                recipe_origin="memory",
                recipe_version=stored.recipe.version if stored is not None else None,
            )
            for i, stored in enumerate(pinned, start=1)
        )
    sources: list[JobSource] = []
    selections: list[RecipeSelection] = []
    for index, item in enumerate(job.sources, start=1):
        declared = item.recipe or job.recipe
        serving = repository.get(source_key(job, item)) if repository is not None else None
        stored = serving.recipe.version if serving is not None else None
        if job.recipe_memory is not None and memory_wins(
            declared.version if declared else None, stored
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
    """Select each source's recipe, reading ``recipe_memory`` when the file exists.

    A current-schema store is opened read-only. An older store is opened writable,
    which migrates it, as the run that follows would do anyway. Symlinked
    ``recipe_memory`` paths are refused.
    """
    validate_job_paths(job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths)
    memory_path = recipe_memory_path(
        job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    if memory_path is None or not memory_path.is_file():
        return select_recipes(job, None)
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    try:
        return select_recipes(job, SQLiteRecipeRepository(memory_path, read_only=True))
    except RecipeStorageError:
        return select_recipes(job, SQLiteRecipeRepository(memory_path))
