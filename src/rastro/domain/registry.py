"""Registry domain models, trust indicators and semver ordering (HUB-002..HUB-006)."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from rastro.domain.models import Contract
from rastro.domain.recipe_bundle import (
    BUNDLE_ID_PATTERN,
    SEMVER_PATTERN,
    SPDX_PATTERN,
    BundleAuthor,
    BundleCompatibility,
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


def matches_query(entry: RegistryEntry, query: str) -> bool:
    """Case-insensitive substring match against ``id`` or ``source_patterns``."""
    needle = query.strip().lower()
    return (
        not needle
        or needle in entry.id.lower()
        or any(needle in pattern.lower() for pattern in entry.source_patterns)
    )
