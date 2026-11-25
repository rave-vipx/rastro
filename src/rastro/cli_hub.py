"""Recipe bundle and registry CLI: export, import, publish, search, install."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from rastro.application.recipe_hub import export_bundle, import_bundle
from rastro.application.recipe_registry import (
    install_from_registry,
    publish_to_registry,
    search_registry,
)
from rastro.domain.models import ConfigurationError, Representation
from rastro.domain.recipe_bundle import BundleAuthor
from rastro.ports.parser import DocumentParser

HUB_COMMANDS = frozenset({"export", "import", "publish", "search", "install"})


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    export = commands.add_parser("export", help="Write a recipe bundle for sharing in Git")
    export.add_argument("file", type=Path, help="Job file whose recipe memory holds the recipe")
    export.add_argument("--source", type=int, default=1, help="One-based source index")
    export.add_argument("--sample", type=Path, required=True, help="Redistributable sample file")
    export.add_argument("--to", type=Path, required=True, help="Bundle directory")
    export.add_argument("--id", dest="bundle_id", help="Package identifier")
    export.add_argument("--version", dest="package_version", help="Semver MAJOR.MINOR.PATCH")
    export.add_argument("--author", help="Package author name")
    export.add_argument("--author-url", help="Optional package author URL")
    export.add_argument("--license", dest="spdx_license", help="SPDX license identifier")
    export.add_argument("--public", action="store_true", help="Mark bundle as public")
    export.add_argument(
        "--allow-absolute-paths", action="store_true", help="Allow absolute paths in the job file"
    )

    imported = commands.add_parser(
        "import", help="Verify a recipe bundle and store it as a candidate"
    )
    imported.add_argument("directory", type=Path, help="Bundle directory to import")
    imported.add_argument("--store", type=Path, required=True, help="Recipe SQLite file")

    publish = commands.add_parser(
        "publish", help="Verify a public bundle and write it into a local Git registry directory"
    )
    publish.add_argument("directory", type=Path, help="Exported bundle directory")
    publish.add_argument("--registry", type=Path, required=True, help="Local registry directory")

    search = commands.add_parser(
        "search", help="Search a Git registry (directory or https URL) by id or source pattern"
    )
    search.add_argument("query", help="Substring to match against id or source_patterns")
    _registry_location(search)

    install = commands.add_parser(
        "install", help="Verify and install a recipe package from a Git registry as a candidate"
    )
    install.add_argument("package", help="Package id or id@version")
    _registry_location(install)
    install.add_argument("--store", type=Path, required=True, help="Recipe SQLite file")


def _registry_location(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--registry", type=Path, help="Local registry directory")
    where.add_argument(
        "--registry-url",
        metavar="URL",
        help="https URL that serves the registry directory (index.json and recipes/); no "
        "credentials or query. Needs the [http] extra; never promotes",
    )


def hub_command(args: argparse.Namespace) -> int:
    handlers = {
        "export": _export,
        "import": _import,
        "publish": _publish,
        "search": _search,
        "install": _install,
    }
    return handlers[args.recipe_command](args)


def _parser_for(representation: Representation) -> DocumentParser:
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.structured import JsonParser

    return JsonParser() if representation == "json" else ScraplingParser()


def _author(args: argparse.Namespace) -> BundleAuthor | None:
    if args.author is None and args.author_url is None:
        return None
    if args.author is None:
        raise ConfigurationError("--author-url requires --author")
    try:
        return BundleAuthor(name=args.author, url=args.author_url)
    except ValidationError as error:
        raise ConfigurationError("Bundle author metadata is invalid") from error


def _export(args: argparse.Namespace) -> int:
    from rastro.application.hub_publish import serving_source_recipe

    found = serving_source_recipe(
        args.file, args.source, allow_absolute_paths=args.allow_absolute_paths
    )
    manifest = export_bundle(
        args.to,
        recipe=found.stored.validated_recipe,
        source=found.source,
        context=found.context,
        fields=found.job.fields,
        sample=args.sample,
        parser=_parser_for(found.item.representation),
        verified_on=datetime.now(UTC).date(),
        limits=found.job.limits,
        bundle_id=args.bundle_id,
        version=args.package_version,
        author=_author(args),
        license=args.spdx_license,
        public=bool(args.public),
    )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "format_version": manifest.format_version,
                "visibility": manifest.visibility,
                "files": sorted(manifest.files),
            }
        )
    )
    return 0


def _import(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    if args.store.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    imported = import_bundle(
        args.directory, SQLiteRecipeRepository(args.store), _parser_for, at=datetime.now(UTC)
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


def _publish(args: argparse.Namespace) -> int:
    published = publish_to_registry(
        args.directory, args.registry, _parser_for, today=datetime.now(UTC).date()
    )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "id": published.entry.id,
                "version": published.entry.version,
                "trust": published.entry.trust.model_dump(mode="json"),
                "git_commands": list(published.git_commands),
            }
        )
    )
    return 0


def _search(args: argparse.Namespace) -> int:
    if args.registry_url is not None:
        from rastro.application.registry_fetch import search_registry_url

        entries = search_registry_url(args.registry_url, args.query)
    else:
        entries = search_registry(args.registry, args.query)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "query": args.query,
                "results": [entry.model_dump(mode="json") for entry in entries],
            }
        )
    )
    return 0


def _install(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    if args.store.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    repository, at = SQLiteRecipeRepository(args.store), datetime.now(UTC)
    if args.registry_url is not None:
        from rastro.application.registry_fetch import install_from_registry_url

        installed = install_from_registry_url(
            args.registry_url, args.package, repository, _parser_for, at=at
        )
    else:
        installed = install_from_registry(
            args.registry, args.package, repository, _parser_for, at=at
        )
    print(
        json.dumps(
            {
                "schema_version": 1,
                "id": installed.id,
                "package_version": installed.version,
                "state": installed.imported.stored.metadata.state,
                "version": installed.imported.stored.recipe.version,
                "reused": installed.imported.reused,
                "trust": installed.trust.model_dump(mode="json"),
                "published_trust": installed.published_trust.model_dump(mode="json"),
            }
        )
    )
    return 0
