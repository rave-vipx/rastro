"""Registry directory layout, index reading and atomic index writes (HUB-003, HUB-005).

The registry is a plain Git checkout: never follow symlinks inside it.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from rastro.application.atomic_files import fsync_directory
from rastro.application.bundle_files import read_bounded
from rastro.domain.recipe_bundle import MAX_METADATA_BYTES
from rastro.domain.registry import INDEX_FILE, RECIPES_DIR, RegistryEntry, RegistryError

_INDEX_ADAPTER = TypeAdapter(tuple[RegistryEntry, ...])


def check_registry_root(registry_dir: Path, *, create: bool) -> None:
    if registry_dir.is_symlink():
        raise RegistryError("Registry path must not be a symlink")
    if not registry_dir.exists():
        if not create:
            raise RegistryError("Registry directory does not exist")
        registry_dir.mkdir(parents=True, exist_ok=True)
    if not registry_dir.is_dir() or (registry_dir / RECIPES_DIR).is_symlink():
        raise RegistryError("Registry path must be a real directory without symlinks")


def package_path(registry_dir: Path, pkg_id: str, version: str) -> Path:
    pkg_dir = registry_dir / RECIPES_DIR / pkg_id / version
    if pkg_dir.parent.is_symlink() or pkg_dir.is_symlink():
        raise RegistryError("Registry package directory must not be a symlink")
    return pkg_dir


def load_index(registry_dir: Path) -> tuple[RegistryEntry, ...]:
    index_path = registry_dir / INDEX_FILE
    if not index_path.exists() and not index_path.is_symlink():
        return ()
    return parse_index(read_bounded(index_path, MAX_METADATA_BYTES, bundle=True))


def parse_index(raw_bytes: bytes) -> tuple[RegistryEntry, ...]:
    """Validate ``index.json`` bytes; ids and versions are checked against their patterns, so
    an entry can never carry a path separator or ``..`` into a package path."""
    try:
        return _INDEX_ADAPTER.validate_python(json.loads(raw_bytes))
    except (ValueError, RecursionError, ValidationError) as error:
        raise RegistryError("Registry index.json is invalid") from error


def write_index_atomic(path: Path, data: bytes) -> None:
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
        with suppress(OSError):  # the index is already replaced; only durability is best-effort
            fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)
