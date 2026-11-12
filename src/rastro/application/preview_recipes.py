"""Read-only recipe resolution for previews of jobs with ``recipe_memory`` (DATA-002, REC-001).

A preview must not create, migrate or write the recipe store. Each source gets an
explicit recipe: the stored serving recipe when it is newer than the job file's
recipe, otherwise the job file's recipe. The returned job carries no memory, so
the run that follows has no repository to write to.
"""

from __future__ import annotations

from pathlib import Path

import rastro
from rastro.application.job_loader import resolve_job_path
from rastro.domain.jobs import FileJobSource, Job
from rastro.domain.memory import RecipeKey
from rastro.domain.models import ConfigurationError, Recipe


def _pick(job_recipe: Recipe | None, stored: Recipe | None) -> Recipe | None:
    if stored is not None and (job_recipe is None or stored.version > job_recipe.version):
        return stored
    return job_recipe


def read_only_job(job: Job, *, base_dir: Path, allow_absolute_paths: bool = False) -> Job:
    """Return ``job`` with per-source recipes resolved from a read-only store, and no memory."""
    if job.recipe_memory is None:
        return job
    path = resolve_job_path(
        job.recipe_memory, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    repository = rastro.SQLiteRecipeRepository(path, read_only=True) if path.is_file() else None
    if repository is None and job.recipe_version is not None:
        raise ConfigurationError("recipe_version needs an existing recipe_memory file")
    schema = job.build_schema()
    sources = []
    for item in job.sources:
        url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
        key = RecipeKey.for_source(
            (item.region or job.region).to_source(url),
            schema,
            job.context,
            representation=item.representation,
        )
        stored = repository.get(key, version=job.recipe_version) if repository else None
        if job.recipe_version is not None and stored is None:
            raise ConfigurationError("No stored recipe matches recipe_version")
        recipe = _pick(item.recipe or job.recipe, stored.validated_recipe if stored else None)
        if recipe is None and not (job.discover_recipes or job.model is not None):
            raise ConfigurationError(
                "No recipe for a preview source: add a recipe or load the job once"
            )
        sources.append(item.model_copy(update={"recipe": recipe}))
    return job.model_copy(
        update={
            "sources": tuple(sources),
            "recipe": None,
            "recipe_memory": None,
            "recipe_version": None,
            "context": None,
        }
    )
