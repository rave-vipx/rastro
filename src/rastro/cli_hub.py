"""Recipe bundle commands: export a serving recipe, import a verified bundle (HUB-001)."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from rastro.application.recipe_hub import export_bundle, import_bundle
from rastro.application.repair_context import safe_path
from rastro.domain.jobs import FileJobSource
from rastro.domain.memory import RecipeKey
from rastro.domain.models import ConfigurationError, Representation
from rastro.ports.parser import DocumentParser


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    export = commands.add_parser("export", help="Write a recipe bundle for sharing in Git")
    export.add_argument("file", type=Path, help="Job file whose recipe memory holds the recipe")
    export.add_argument("--source", type=int, default=1, help="One-based source index")
    export.add_argument("--sample", type=Path, required=True, help="Redistributable sample file")
    export.add_argument("--to", type=Path, required=True, help="Bundle directory")
    export.add_argument("--allow-absolute-paths", action="store_true")
    imported = commands.add_parser(
        "import", help="Verify a recipe bundle and store it as a candidate"
    )
    imported.add_argument("directory", type=Path)
    imported.add_argument("--store", type=Path, required=True, help="Recipe SQLite file")


def hub_command(args: argparse.Namespace) -> int:
    if args.recipe_command == "export":
        return _export(args)
    return _import(args)


def _parser_for(representation: Representation) -> DocumentParser:
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.structured import JsonParser

    return JsonParser() if representation == "json" else ScraplingParser()


def _export(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.application.job_loader import load_job

    if args.file.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    base = args.file.parent.resolve()
    job = load_job(base / args.file.name, allow_absolute_paths=args.allow_absolute_paths)
    if job.recipe_memory is None:
        raise ConfigurationError("Exporting a recipe requires recipe_memory")
    if not 1 <= args.source <= len(job.sources):
        raise ConfigurationError("Source index is outside the job sources")
    item = job.sources[args.source - 1]
    url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
    source = (item.region or job.region).to_source(url)
    schema = job.build_schema()
    key = RecipeKey.for_source(source, schema, job.context, representation=item.representation)
    memory = safe_path(job.recipe_memory, base, args.allow_absolute_paths)
    stored = SQLiteRecipeRepository(memory).get(key)
    if stored is None:
        raise ConfigurationError("No serving recipe is stored for this source")
    manifest = export_bundle(
        args.to,
        recipe=stored.validated_recipe,
        source=source,
        context=key.context,
        fields=job.fields,
        sample=args.sample,
        parser=_parser_for(item.representation),
        verified_on=datetime.now(UTC).date(),
        limits=job.limits,
    )
    print(json.dumps({"schema_version": 1, "files": sorted(manifest.files)}))
    return 0


def _import(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    if args.store.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    imported = import_bundle(
        args.directory,
        SQLiteRecipeRepository(args.store),
        _parser_for,
        at=datetime.now(UTC),
    )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "state": imported.stored.metadata.state,
                "version": imported.stored.recipe.version,
                "reused": imported.reused,
            }
        )
    )
    return 0
