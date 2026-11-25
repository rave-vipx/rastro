"""Git-directory recipe registry: publish, search and install packages."""

from __future__ import annotations

import json
import shlex
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from rastro.application.bundle_files import read_bounded, write_all
from rastro.application.recipe_hub import GITATTRIBUTES, ParserFor, verify_bundle
from rastro.application.registry_install import InstalledPackage, install_package_dir
from rastro.application.registry_store import (
    CARD_FILE,
    HubCard,
    check_registry_root,
    load_index,
    package_path,
    write_index_atomic,
)
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.recipe_bundle import (
    MANIFEST_FILE,
    MAX_METADATA_BYTES,
    SAMPLE_FILES,
    BundleManifest,
    canonical_json,
    default_source_pattern,
)
from rastro.domain.registry import (
    INDEX_FILE,
    RECIPES_DIR,
    RegistryEntry,
    RegistryError,
    compute_trust,
    matches_query,
    parse_package_spec,
    select_entry,
    semver_key,
)
from rastro.mocklab.detectors import secret_categories
from rastro.ports.recipes import RecipeRepository


@dataclass(frozen=True)
class PublishedPackage:
    """Result of publishing a verified bundle into a local registry directory."""

    entry: RegistryEntry
    package_dir: Path
    git_commands: tuple[str, ...]


def publish_to_registry(
    bundle_dir: Path,
    registry_dir: Path,
    parser_for: ParserFor,
    *,
    today: date,
    limits: Limits | None = None,
    card: HubCard | None = None,
) -> PublishedPackage:
    """Verify a public bundle, scan for secrets, copy files and atomically rewrite index.json.

    A ``card`` (field descriptions and engine needs) is scanned too and written as
    ``card.json`` in the same package directory, so it lands or fails with the package."""
    manifest, files, _, _ = verify_bundle(bundle_dir, parser_for, limits)
    entry = _build_entry(manifest, files, today=today)
    extra = {} if card is None else {CARD_FILE: canonical_json(card.model_dump(mode="json"))}
    _scan_for_secrets(manifest, {**files, **extra})
    check_registry_root(registry_dir, create=True)
    existing = load_index(registry_dir)
    if any(item.id == entry.id and item.version == entry.version for item in existing):
        raise RegistryError(f"Package {entry.id}@{entry.version} already exists in the registry")
    package_dir = package_path(registry_dir, entry.id, entry.version)
    if package_dir.exists() or package_dir.is_symlink():
        raise RegistryError(f"Package directory for {entry.id}@{entry.version} already exists")
    raw_manifest = read_bounded(bundle_dir / MANIFEST_FILE, MAX_METADATA_BYTES, bundle=True)
    updated = sorted((*existing, entry), key=lambda item: (item.id, semver_key(item.version)))
    contents = {**files, **extra, MANIFEST_FILE: raw_manifest, GITATTRIBUTES: b"* -text\n"}
    index = canonical_json([item.model_dump(mode="json") for item in updated])
    _write_package(registry_dir, package_dir, contents, index)
    commands = _git_commands(registry_dir, entry)
    return PublishedPackage(entry=entry, package_dir=package_dir, git_commands=commands)


def _write_package(
    registry_dir: Path, package_dir: Path, contents: Mapping[str, bytes], index: bytes
) -> None:
    try:
        write_all(package_dir, dict(contents))
        write_index_atomic(registry_dir / INDEX_FILE, index)
    except (OSError, ConfigurationError) as error:
        # Only this publish created package_dir (the caller checked it was absent); remove it
        # so a retry is not blocked. The index is replaced atomically, so it never lists it.
        shutil.rmtree(package_dir, ignore_errors=True)
        if isinstance(error, ConfigurationError):
            raise
        raise RegistryError("Registry could not be updated; nothing was published") from error


def _git_commands(registry_dir: Path, entry: RegistryEntry) -> tuple[str, ...]:
    root = shlex.quote(str(registry_dir))
    package = shlex.quote(f"{RECIPES_DIR}/{entry.id}/{entry.version}")
    message = shlex.quote(f"Publish {entry.id} {entry.version}")
    return (
        f"git -C {root} add {INDEX_FILE} {package}",
        f"git -C {root} commit -m {message}",
        f"git -C {root} push",
    )


def search_registry(registry_dir: Path, query: str) -> tuple[RegistryEntry, ...]:
    """Return registry entries matching ``query`` on ``id`` or ``source_patterns``."""
    check_registry_root(registry_dir, create=False)
    matched = [entry for entry in load_index(registry_dir) if matches_query(entry, query)]
    return tuple(sorted(matched, key=lambda item: (item.id, semver_key(item.version))))


def install_from_registry(
    registry_dir: Path,
    spec: str,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
    engine_version: str | None = None,
) -> InstalledPackage:
    """Resolve ``ID[@VERSION]``, check compatibility, recompute trust, verify and import.

    The package must fit the running engine (``min_rastro``) and its own source patterns;
    ``engine_version`` overrides the installed version for tests.
    """
    pkg_id, pinned = parse_package_spec(spec)
    check_registry_root(registry_dir, create=False)
    selected = select_entry(load_index(registry_dir), pkg_id, pinned)
    package_dir = package_path(registry_dir, selected.id, selected.version)
    return install_package_dir(
        package_dir,
        selected,
        repository,
        parser_for,
        at=at,
        limits=limits,
        engine_version=engine_version,
    )


def _build_entry(
    manifest: BundleManifest, files: Mapping[str, bytes], *, today: date
) -> RegistryEntry:
    if manifest.visibility != "public":
        raise RegistryError(
            "Private recipes are never published; re-export the bundle with --public",
            hint="Re-export with `rastro recipe export ... --public` and package metadata",
        )
    req = (manifest.id, manifest.version, manifest.author, manifest.license)
    if manifest.format_version != 2 or any(item is None for item in req):
        raise RegistryError(
            "Publishing requires a format-2 bundle with --id, --version, --author and --license"
        )
    assert manifest.id and manifest.version and manifest.author and manifest.license
    patterns = (
        manifest.compatibility.source_patterns
        if manifest.compatibility is not None
        else (default_source_pattern(manifest.source.url),)
    )
    trust = compute_trust(
        replay_verified=True,
        last_verified=manifest.last_verified,
        today=today,
        has_sample=bool(files.get(SAMPLE_FILES[manifest.representation])),
        format_version=manifest.format_version,
    )
    return RegistryEntry(
        id=manifest.id,
        version=manifest.version,
        author=manifest.author,
        license=manifest.license,
        source_patterns=patterns,
        last_verified=manifest.last_verified,
        trust=trust,
    )


def _scan_for_secrets(manifest: BundleManifest, files: Mapping[str, bytes]) -> None:
    manifest_scan = {k: v for k, v in manifest.model_dump(mode="json").items() if k != "files"}
    items = [(MANIFEST_FILE, json.dumps(manifest_scan, sort_keys=True))] + [
        (name, data.decode("utf-8")) for name, data in sorted(files.items())
    ]
    for name, text in items:
        found = secret_categories(text, include_opaque=False)
        if found:
            raise RegistryError(
                f"Bundle file {name} matches secret or personal-data patterns: {', '.join(found)}"
            )
