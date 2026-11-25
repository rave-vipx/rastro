"""Publish a job's serving recipe to a hub checkout in one step.

The package is the verified bundle, the operator's synthetic sample, a description of every
field and the engine needs computed from the recipe and source. Everything is written into a
local registry directory for a pull request: no network write, no account, no telemetry.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from rastro.application.capability_gate import model_capabilities
from rastro.application.recipe_hub import ParserFor, export_bundle
from rastro.application.recipe_registry import PublishedPackage, publish_to_registry
from rastro.application.registry_store import EngineNeeds, FieldNote, HubCard
from rastro.application.repair_context import safe_path
from rastro.domain.job_sources import FileJobSource, JobSource, UrlJobSource
from rastro.domain.jobs import Job
from rastro.domain.memory import RecipeContext, RecipeKey, StoredRecipe
from rastro.domain.models import ConfigurationError, Source
from rastro.domain.recipe_bundle import BundleAuthor


@dataclass(frozen=True)
class PackageInfo:
    """Package metadata a hub entry needs."""

    id: str
    version: str
    author: str
    license: str
    author_url: str | None = None


@dataclass(frozen=True)
class SourceRecipe:
    """The serving recipe stored for one job source, with what it was stored under."""

    job: Job
    item: JobSource
    source: Source
    context: RecipeContext
    stored: StoredRecipe


@dataclass(frozen=True)
class HubPublication:
    published: PublishedPackage
    card: HubCard


def serving_source_recipe(
    job_file: Path, source_index: int, *, allow_absolute_paths: bool = False
) -> SourceRecipe:
    """Load ``job_file`` and the recipe its ``recipe_memory`` serves for one source."""
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.application.job_loader import load_job

    if job_file.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    base = job_file.parent.resolve()
    job = load_job(base / job_file.name, allow_absolute_paths=allow_absolute_paths)
    if job.recipe_memory is None:
        raise ConfigurationError("Exporting a recipe requires recipe_memory")
    if not 1 <= source_index <= len(job.sources):
        raise ConfigurationError("Source index is outside the job sources")
    item = job.sources[source_index - 1]
    url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
    source = (item.region or job.region).to_source(url)
    key = RecipeKey.for_source(
        source, job.build_schema(), job.context, representation=item.representation
    )
    memory = safe_path(job.recipe_memory, base, allow_absolute_paths)
    stored = SQLiteRecipeRepository(memory).get(key)
    if stored is None:
        raise ConfigurationError("No serving recipe is stored for this source")
    return SourceRecipe(job, item, source, key.context, stored)


def build_card(found: SourceRecipe, descriptions: Mapping[str, str]) -> HubCard:
    """Every top-level field described (and nothing else), plus the recipe's engine needs."""
    names = [field.name for field in found.job.fields]
    missing = [name for name in names if name not in descriptions]
    unknown = sorted(set(descriptions) - set(names))
    if missing or unknown:
        parts = [f"missing: {', '.join(missing)}"] if missing else []
        parts += [f"not fields of this job: {', '.join(unknown)}"] if unknown else []
        raise ConfigurationError(
            "Describe every field to publish (" + "; ".join(parts) + ")",
            hint="Pass --describe FIELD=TEXT once per field",
        )
    recipe = found.stored.validated_recipe
    render = found.item.render if isinstance(found.item, UrlJobSource) else "http"
    capabilities = set(model_capabilities(recipe)) | set(model_capabilities(found.item))
    if render == "stealth":
        capabilities.add("stealth")
    try:
        return HubCard(
            fields=tuple(FieldNote(name=n, description=descriptions[n]) for n in names),
            engine_needs=EngineNeeds(
                representation=recipe.representation,
                render=render,
                capabilities=tuple(sorted(capabilities)),
            ),
        )
    except ValidationError as error:
        raise ConfigurationError(
            "Field descriptions must be 1-500 printable characters on one line"
        ) from error


def publish_job_to_hub(
    job_file: Path,
    registry_dir: Path,
    parser_for: ParserFor,
    *,
    source_index: int,
    sample: Path,
    package: PackageInfo,
    descriptions: Mapping[str, str],
    today: date,
    allow_absolute_paths: bool = False,
) -> HubPublication:
    """Export, verify, scan and write the package with its card into ``registry_dir``.

    Every gate of ``rastro recipe export`` and ``publish`` applies; a refusal writes nothing.
    """
    found = serving_source_recipe(job_file, source_index, allow_absolute_paths=allow_absolute_paths)
    card = build_card(found, descriptions)
    try:
        author = BundleAuthor(name=package.author, url=package.author_url)
    except ValidationError as error:
        raise ConfigurationError("Bundle author metadata is invalid") from error
    with tempfile.TemporaryDirectory(prefix="rastro-hub-") as scratch:
        bundle = Path(scratch) / "bundle"
        export_bundle(
            bundle,
            recipe=found.stored.validated_recipe,
            source=found.source,
            context=found.context,
            fields=found.job.fields,
            sample=sample,
            parser=parser_for(found.item.representation),
            verified_on=today,
            limits=found.job.limits,
            bundle_id=package.id,
            version=package.version,
            author=author,
            license=package.license,
            public=True,
        )
        published = publish_to_registry(bundle, registry_dir, parser_for, today=today, card=card)
    return HubPublication(published, card)
