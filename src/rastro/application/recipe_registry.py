"""Git-directory recipe registry: publish, search and install packages (HUB-003..HUB-006)."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from rastro.application.atomic_files import fsync_directory
from rastro.application.recipe_hub import (
    GITATTRIBUTES,
    ImportedRecipe,
    ParserFor,
    import_bundle,
    read_bounded,
    verify_bundle,
    write_all,
)
from rastro.domain.models import Limits
from rastro.domain.recipe_bundle import (
    MANIFEST_FILE,
    MAX_METADATA_BYTES,
    SAMPLE_FILES,
    BundleIntegrityError,
    BundleManifest,
    canonical_json,
    default_source_pattern,
)
from rastro.domain.registry import (
    INDEX_FILE,
    RECIPES_DIR,
    RegistryEntry,
    RegistryError,
    TrustIndicators,
    compute_trust,
    matches_query,
    parse_package_spec,
    semver_key,
)
from rastro.mocklab.detectors import secret_categories
from rastro.ports.recipes import RecipeRepository

_INDEX_ADAPTER = TypeAdapter(tuple[RegistryEntry, ...])


@dataclass(frozen=True)
class PublishedPackage:
    """Result of publishing a verified bundle into a local registry directory."""

    entry: RegistryEntry
    package_dir: Path
    git_commands: tuple[str, ...]


@dataclass(frozen=True)
class InstalledPackage:
    """Result of installing a recipe package from a local registry directory."""

    id: str
    version: str
    imported: ImportedRecipe
    trust: TrustIndicators
    published_trust: TrustIndicators


def publish_to_registry(
    bundle_dir: Path,
    registry_dir: Path,
    parser_for: ParserFor,
    *,
    today: date,
    limits: Limits | None = None,
) -> PublishedPackage:
    """Verify a public bundle, scan for secrets, copy files and atomically rewrite index.json."""
    manifest, files, _, _ = verify_bundle(bundle_dir, parser_for, limits)
    entry = _build_entry(manifest, files, today=today)
    _scan_for_secrets(manifest, files)
    _check_registry_root(registry_dir, create=True)
    existing = _load_index(registry_dir)
    if any(item.id == entry.id and item.version == entry.version for item in existing):
        raise RegistryError(f"Package {entry.id}@{entry.version} already exists in the registry")
    package_dir = _package_path(registry_dir, entry.id, entry.version)
    if package_dir.exists() or package_dir.is_symlink():
        raise RegistryError(f"Package directory for {entry.id}@{entry.version} already exists")
    raw_manifest = read_bounded(bundle_dir / MANIFEST_FILE, MAX_METADATA_BYTES, bundle=True)
    write_all(package_dir, {**files, MANIFEST_FILE: raw_manifest, GITATTRIBUTES: b"* -text\n"})
    updated = sorted((*existing, entry), key=lambda item: (item.id, semver_key(item.version)))
    _write_index_atomic(
        registry_dir / INDEX_FILE,
        canonical_json([item.model_dump(mode="json") for item in updated]),
    )
    rel_pkg = f"{RECIPES_DIR}/{entry.id}/{entry.version}"
    commands = (
        f"git -C {registry_dir} add {INDEX_FILE} {rel_pkg}",
        f'git -C {registry_dir} commit -m "Publish {entry.id} {entry.version}"',
        f"git -C {registry_dir} push",
    )
    return PublishedPackage(entry=entry, package_dir=package_dir, git_commands=commands)


def search_registry(registry_dir: Path, query: str) -> tuple[RegistryEntry, ...]:
    """Return registry entries matching ``query`` on ``id`` or ``source_patterns``."""
    _check_registry_root(registry_dir, create=False)
    matched = [entry for entry in _load_index(registry_dir) if matches_query(entry, query)]
    return tuple(sorted(matched, key=lambda item: (item.id, semver_key(item.version))))


def install_from_registry(
    registry_dir: Path,
    spec: str,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
) -> InstalledPackage:
    """Resolve ``ID[@VERSION]``, recompute trust, verify and import into ``repository``."""
    pkg_id, pinned = parse_package_spec(spec)
    _check_registry_root(registry_dir, create=False)
    candidates = [item for item in _load_index(registry_dir) if item.id == pkg_id]
    if not candidates:
        raise RegistryError(f"Package {pkg_id!r} was not found in the registry")
    selected = (
        next((item for item in candidates if item.version == pinned), None)
        if pinned is not None
        else max(candidates, key=lambda item: semver_key(item.version))
    )
    if selected is None:
        raise RegistryError(f"Package {pkg_id}@{pinned} was not found in the registry")
    package_dir = _package_path(registry_dir, selected.id, selected.version)
    manifest, files, _, _ = verify_bundle(package_dir, parser_for, limits)
    if manifest.id != selected.id or manifest.version != selected.version:
        raise BundleIntegrityError("Package manifest does not match its registry index entry")
    recomputed = compute_trust(
        replay_verified=True,
        last_verified=manifest.last_verified,
        today=at.date(),
        has_sample=bool(files.get(SAMPLE_FILES[manifest.representation])),
        format_version=manifest.format_version,
    )
    imported = import_bundle(package_dir, repository, parser_for, at=at, limits=limits)
    return InstalledPackage(
        id=selected.id,
        version=selected.version,
        imported=imported,
        trust=recomputed,
        published_trust=selected.trust,
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


def _check_registry_root(registry_dir: Path, *, create: bool) -> None:
    if registry_dir.is_symlink():
        raise RegistryError("Registry path must not be a symlink")
    if not registry_dir.exists():
        if not create:
            raise RegistryError("Registry directory does not exist")
        registry_dir.mkdir(parents=True, exist_ok=True)
    if not registry_dir.is_dir() or (registry_dir / RECIPES_DIR).is_symlink():
        raise RegistryError("Registry path must be a real directory without symlinks")


def _package_path(registry_dir: Path, pkg_id: str, version: str) -> Path:
    pkg_dir = registry_dir / RECIPES_DIR / pkg_id / version
    if pkg_dir.parent.is_symlink() or pkg_dir.is_symlink():
        raise RegistryError("Registry package directory must not be a symlink")
    return pkg_dir


def _load_index(registry_dir: Path) -> tuple[RegistryEntry, ...]:
    index_path = registry_dir / INDEX_FILE
    if not index_path.exists() and not index_path.is_symlink():
        return ()
    raw_bytes = read_bounded(index_path, MAX_METADATA_BYTES, bundle=True)
    try:
        return _INDEX_ADAPTER.validate_python(json.loads(raw_bytes))
    except (ValueError, RecursionError, ValidationError) as error:
        raise RegistryError("Registry index.json is invalid") from error


def _write_index_atomic(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise RegistryError("Registry index.json must not be a symlink")
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(temp, flags, 0o644), "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)
