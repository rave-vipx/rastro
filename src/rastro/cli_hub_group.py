"""``rastro hub``: publish to, search and install from the shared recipe hub.

The hub is the public registry by default (``DEFAULT_HUB_URL``), or any directory or https URL
with the same layout. Search offers candidates for a URL's domain; install stores a candidate;
publish writes a package into a local checkout for a pull request and never uses the network.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from rastro.application.registry_url import DEFAULT_HUB_URL
from rastro.domain.models import ConfigurationError

_REGISTRY_URL_HELP = f"https registry URL (default: the public hub, {DEFAULT_HUB_URL})"


def add_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    summary = "Share recipes through the hub: publish, search by URL, install a candidate"
    parser = subcommands.add_parser("hub", help=summary, description=summary)
    commands = parser.add_subparsers(dest="hub_command", required=True)

    publish = commands.add_parser(
        "publish",
        help="Write a job's recipe, sample, field descriptions and engine needs "
        "into a local hub checkout, ready for a pull request",
    )
    publish.add_argument("file", type=Path, help="Job file whose recipe memory holds the recipe")
    publish.add_argument("--source", type=int, default=1, help="One-based source index")
    publish.add_argument(
        "--sample", type=Path, required=True, help="Synthetic sample you may redistribute"
    )
    publish.add_argument("--registry", type=Path, required=True, help="Local hub checkout")
    publish.add_argument("--id", dest="package_id", required=True, help="Package identifier")
    publish.add_argument("--version", dest="package_version", required=True, help="Semver")
    publish.add_argument("--author", required=True, help="Package author name")
    publish.add_argument("--author-url", help="Optional package author URL")
    publish.add_argument("--license", dest="spdx_license", required=True, help="SPDX license")
    publish.add_argument(
        "--describe",
        action="append",
        default=[],
        metavar="FIELD=TEXT",
        help="What a field holds; once per field of the job",
    )
    publish.add_argument(
        "--allow-absolute-paths", action="store_true", help="Allow absolute paths in the job file"
    )

    search = commands.add_parser("search", help="Offer hub recipes for a URL's domain")
    search.add_argument("url", help="The page you want to scrape")
    _where(search)

    install = commands.add_parser("install", help="Install a hub package as a candidate")
    install.add_argument("package", help="Package id or id@version")
    install.add_argument("--store", type=Path, required=True, help="Recipe SQLite file")
    _where(install)


def _where(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--registry", type=Path, help="Local hub checkout instead of a URL")
    where.add_argument("--registry-url", metavar="URL", help=_REGISTRY_URL_HELP)


def hub_group_command(args: argparse.Namespace) -> int:
    handlers = {"publish": _publish, "search": _search, "install": _install}
    return handlers[args.hub_command](args)


def _descriptions(items: list[str]) -> dict[str, str]:
    described: dict[str, str] = {}
    for item in items:
        name, sep, text = item.partition("=")
        if not sep or not name.strip():
            raise ConfigurationError("--describe takes FIELD=TEXT")
        described[name.strip()] = text.strip()
    return described


def _publish(args: argparse.Namespace) -> int:
    from rastro.application.hub_publish import PackageInfo, publish_job_to_hub
    from rastro.cli_hub import _parser_for

    descriptions = _descriptions(args.describe)
    result = publish_job_to_hub(
        args.file,
        args.registry,
        _parser_for,
        source_index=args.source,
        sample=args.sample,
        package=PackageInfo(
            id=args.package_id,
            version=args.package_version,
            author=args.author,
            license=args.spdx_license,
            author_url=args.author_url,
        ),
        descriptions=descriptions,
        today=datetime.now(UTC).date(),
        allow_absolute_paths=args.allow_absolute_paths,
    )
    entry = result.published.entry
    payload = {
        "schema_version": 1,
        "id": entry.id,
        "version": entry.version,
        "package_dir": str(result.published.package_dir),
        "engine_needs": result.card.engine_needs.model_dump(mode="json"),
        "git_commands": list(result.published.git_commands),
    }
    print(json.dumps(payload))
    return 0


def _registry(args: argparse.Namespace) -> Path | str:
    return args.registry if args.registry is not None else args.registry_url or DEFAULT_HUB_URL


def _search(args: argparse.Namespace) -> int:
    from rastro.application.hub_search import hub_candidates

    offers = hub_candidates(args.url, registry=_registry(args))
    print(json.dumps({"schema_version": 1, "candidates": [o.to_json() for o in offers]}))
    return 0


def _install(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.application.recipe_registry import install_from_registry
    from rastro.application.registry_fetch import install_from_registry_url
    from rastro.cli_hub import _parser_for

    if args.store.is_symlink():
        raise ConfigurationError("Recipe command paths must not contain symlinks")
    repository, at, where = SQLiteRecipeRepository(args.store), datetime.now(UTC), _registry(args)
    if isinstance(where, Path):
        installed = install_from_registry(where, args.package, repository, _parser_for, at=at)
    else:
        installed = install_from_registry_url(where, args.package, repository, _parser_for, at=at)
    stored = installed.imported.stored
    payload = {
        "schema_version": 1,
        "id": installed.id,
        "package_version": installed.version,
        "state": stored.metadata.state,
        "version": stored.recipe.version,
        "reused": installed.imported.reused,
        "trust": installed.trust.model_dump(mode="json"),
    }
    print(json.dumps(payload))
    return 0
