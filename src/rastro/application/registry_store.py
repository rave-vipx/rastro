"""Registry directory layout, index reading and atomic index writes (HUB-003, HUB-005).

The registry is a plain Git checkout: never follow symlinks inside it. Besides ``index.json``
and the bundles it may hold two optional files, both untrusted and validated like the index:
``recipes/<id>/<version>/card.json`` (field descriptions and engine needs, written by
publish) and a root ``health.json`` (last verification per package, written by the hub's CI
and only read here).
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from rastro.application.atomic_files import fsync_directory
from rastro.application.bundle_files import read_bounded
from rastro.domain.lifecycle_history import Reason
from rastro.domain.recipe_bundle import BUNDLE_ID_PATTERN, MAX_METADATA_BYTES, SEMVER_PATTERN
from rastro.domain.registry import INDEX_FILE, RECIPES_DIR, RegistryEntry, RegistryError
from rastro.domain.sources import Contract

CARD_FILE = "card.json"
HEALTH_FILE = "health.json"
MAX_CARD_BYTES = 64 * 1024
_INDEX_ADAPTER = TypeAdapter(tuple[RegistryEntry, ...])


class FieldNote(Contract):
    """What one top-level field holds, in the publisher's words (display only)."""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: Reason


class EngineNeeds(Contract):
    """What the recipe needs to run; install still decides grants on its own."""

    representation: Literal["html", "json"]
    render: Literal["http", "browser", "stealth"]
    capabilities: tuple[str, ...] = Field(max_length=16)


class HubCard(Contract):
    """``card.json``: the human side of a package."""

    format: Literal[1] = 1
    fields: tuple[FieldNote, ...] = Field(min_length=1, max_length=64)
    engine_needs: EngineNeeds


class PackageHealth(Contract):
    """One package's last verification by the hub's CI (``health.json``)."""

    id: str = Field(pattern=BUNDLE_ID_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    last_verified: date
    rastro_version: str = Field(pattern=SEMVER_PATTERN)
    passed: bool


class HealthReport(Contract):
    format: Literal[1]
    packages: tuple[PackageHealth, ...] = Field(max_length=10_000)


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


def parse_card(raw_bytes: bytes) -> HubCard:
    return _parse(HubCard, raw_bytes, CARD_FILE)


def parse_health(raw_bytes: bytes) -> tuple[PackageHealth, ...]:
    return _parse(HealthReport, raw_bytes, HEALTH_FILE).packages


def _parse[M: BaseModel](model: type[M], raw_bytes: bytes, name: str) -> M:
    try:
        return model.model_validate_json(raw_bytes, strict=True)  # no "yes" -> True coercion
    except (ValueError, ValidationError) as error:
        raise RegistryError(f"Registry {name} is invalid") from error


def load_card(registry_dir: Path, pkg_id: str, version: str) -> HubCard | None:
    path = package_path(registry_dir, pkg_id, version) / CARD_FILE
    if not path.exists() and not path.is_symlink():
        return None
    return parse_card(read_bounded(path, MAX_CARD_BYTES, bundle=True))


def load_health(registry_dir: Path) -> tuple[PackageHealth, ...]:
    path = registry_dir / HEALTH_FILE
    if not path.exists() and not path.is_symlink():
        return ()
    return parse_health(read_bounded(path, MAX_METADATA_BYTES, bundle=True))


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
