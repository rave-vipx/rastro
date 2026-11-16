"""Registry domain models, trust indicators and semver ordering (HUB-002..HUB-006)."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from rastro.domain.models import Contract
from rastro.domain.recipe_bundle import (
    BUNDLE_ID_PATTERN,
    SEMVER_PATTERN,
    SPDX_PATTERN,
    BundleAuthor,
    BundleCompatibility,
    BundleManifest,
    RecipeBundleError,
)

INDEX_FILE = "index.json"
RECIPES_DIR = "recipes"
_ID_RE = re.compile(BUNDLE_ID_PATTERN)
_SEMVER_RE = re.compile(SEMVER_PATTERN)


class RegistryError(RecipeBundleError):
    """A registry operation failed validation or policy checks; never echoes secret values."""


class TrustIndicators(Contract):
    """Reproducible trust indicators computed from offline verification (HUB-006)."""

    replay_verified: bool
    days_since_verified: int = Field(ge=0)
    has_sample: bool
    format: Literal[1, 2]


class RegistryEntry(Contract):
    """One published recipe package entry in ``<registry>/index.json`` (HUB-003, HUB-006)."""

    id: str = Field(pattern=BUNDLE_ID_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    author: BundleAuthor
    license: str = Field(pattern=SPDX_PATTERN)
    source_patterns: tuple[str, ...] = Field(min_length=1, max_length=32)
    last_verified: date
    trust: TrustIndicators

    @field_validator("source_patterns")
    @classmethod
    def validate_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return BundleCompatibility(min_rastro="0.0.0", source_patterns=value).source_patterns


def compute_trust(
    *,
    replay_verified: bool,
    last_verified: date,
    today: date,
    has_sample: bool,
    format_version: Literal[1, 2],
) -> TrustIndicators:
    """Compute trust indicators from current replay result and verification date (HUB-006)."""
    return TrustIndicators(
        replay_verified=replay_verified,
        days_since_verified=max(0, (today - last_verified).days),
        has_sample=has_sample,
        format=format_version,
    )


def semver_key(version: str) -> tuple[int, int, int]:
    """Return numeric ``(major, minor, patch)`` tuple for sorting valid semver strings."""
    if not _SEMVER_RE.fullmatch(version):
        raise RegistryError("Package version must be MAJOR.MINOR.PATCH semver")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def parse_package_spec(spec: str) -> tuple[str, str | None]:
    """Parse ``ID`` or ``ID@VERSION`` and reject invalid or path-traversing identifiers."""
    pkg_id, sep, version = spec.partition("@")
    if not _ID_RE.fullmatch(pkg_id):
        raise RegistryError("Package id must match ^[a-z0-9][a-z0-9-]{1,63}$")
    if sep and not _SEMVER_RE.fullmatch(version):
        raise RegistryError("Pinned version must be MAJOR.MINOR.PATCH semver")
    return pkg_id, (version if sep else None)


def select_entry(
    entries: tuple[RegistryEntry, ...], pkg_id: str, pinned: str | None
) -> RegistryEntry:
    """The pinned version, or the highest semver, of ``pkg_id``; otherwise ``RegistryError``."""
    candidates = [item for item in entries if item.id == pkg_id]
    if not candidates:
        raise RegistryError(f"Package {pkg_id!r} was not found in the registry")
    selected = (
        next((item for item in candidates if item.version == pinned), None)
        if pinned is not None
        else max(candidates, key=lambda item: semver_key(item.version))
    )
    if selected is None:
        raise RegistryError(f"Package {pkg_id}@{pinned} was not found in the registry")
    return selected


def matches_query(entry: RegistryEntry, query: str) -> bool:
    """Case-insensitive substring match against ``id`` or ``source_patterns``."""
    needle = query.strip().lower()
    return (
        not needle
        or needle in entry.id.lower()
        or any(needle in pattern.lower() for pattern in entry.source_patterns)
    )


def source_matches(url: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``url`` is on a pattern's host (and port) and inside its path prefix, if any."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    host = f"{parts.hostname or ''}:{port}" if port else parts.hostname or ""
    path = parts.path.rstrip("/")
    for pattern in patterns:
        pattern_host, _, pattern_path = pattern.partition("/")
        if pattern_host.lower() != host:
            continue
        prefix = f"/{pattern_path}" if pattern_path else ""
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def check_compatibility(manifest: BundleManifest, *, engine_version: str) -> None:
    """Refuse a package that needs a newer engine or does not cover its own source (HUB-002)."""
    compatibility = manifest.compatibility
    if compatibility is None:
        return
    if semver_key(engine_version) < semver_key(compatibility.min_rastro):
        raise RegistryError(
            f"Package requires rastro-engine >= {compatibility.min_rastro}, "
            f"but this is {engine_version}; upgrade rastro-engine"
        )
    if not source_matches(manifest.source.url, compatibility.source_patterns):
        raise RegistryError("Bundle source does not match any of its compatibility source_patterns")
