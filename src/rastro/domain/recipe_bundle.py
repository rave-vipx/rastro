"""Portable recipe bundle format: manifest, canonical bytes and failures."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from rastro.domain.lifecycle_history import check_reason
from rastro.domain.memory import RecipeContext
from rastro.domain.models import ConfigurationError, Contract, Representation, Source

BUNDLE_FORMAT: Literal[2] = 2
SUPPORTED_BUNDLE_FORMATS: frozenset[int] = frozenset({1, 2})
MANIFEST_FILE = "bundle.json"
RECIPE_FILE = "recipe.json"
SCHEMA_FILE = "schema.json"
EXPECTED_FILE = "expected.json"
SAMPLE_FILES: dict[Representation, str] = {"html": "sample.html", "json": "sample.json"}
MAX_METADATA_BYTES = 1_048_576
BUNDLE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"
SEMVER_PATTERN = r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$"
SPDX_PATTERN = r"^[A-Za-z0-9.+-]{1,64}$"
type BundleVisibility = Literal["public", "private"]


class RecipeBundleError(ConfigurationError):
    """A recipe bundle cannot be exported or trusted; messages never echo bundle content."""


class BundleIntegrityError(RecipeBundleError):
    """Missing, unexpected, oversized, linked or altered bundle files."""


class BundleVersionError(RecipeBundleError):
    """The bundle declares a format this release does not understand."""


class BundleVerificationError(RecipeBundleError):
    """The recipe does not reproduce the bundled expected records on the bundled sample."""


class BundleAuthor(Contract):
    """Package author identity."""

    name: str = Field(min_length=1, max_length=128)
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return check_reason(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = AnyHttpUrl(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Author URLs must not contain credentials")
        return str(parsed)


def _validate_source_pattern(pattern: str) -> str:
    if (
        not pattern
        or len(pattern) > 256
        or any(tok in pattern for tok in ("://", "?", "#", "@"))
        or pattern.startswith("/")
        or ".." in pattern.split("/")
        or any(ch.isspace() for ch in pattern)
    ):
        raise ValueError("Source pattern must be a host or host/path-prefix")
    host = pattern.split("/", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", host) or ".." in host:
        raise ValueError("Source pattern host is invalid")
    return pattern.rstrip("/") or host


def default_source_pattern(url: str) -> str:
    """Derive a default ``host`` or ``host/path-prefix`` pattern from a source URL."""
    parts = urlsplit(url)
    host, path = parts.netloc.lower(), parts.path.rstrip("/")
    return f"{host}{path}" if path else host


class BundleCompatibility(Contract):
    """Minimum Rastro version and compatible source host/path patterns."""

    min_rastro: str = Field(pattern=SEMVER_PATTERN)
    source_patterns: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("source_patterns")
    @classmethod
    def validate_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_source_pattern(item) for item in value)


class BundleManifest(Contract):
    """``bundle.json``: what the bundle is for and the digest of every other file."""

    format_version: Literal[1, 2] = BUNDLE_FORMAT
    id: str | None = Field(default=None, pattern=BUNDLE_ID_PATTERN)
    version: str | None = Field(default=None, pattern=SEMVER_PATTERN)
    author: BundleAuthor | None = None
    license: str | None = Field(default=None, pattern=SPDX_PATTERN)
    visibility: BundleVisibility = "private"
    compatibility: BundleCompatibility | None = None
    source: Source
    context: RecipeContext
    representation: Representation
    last_verified: date
    files: dict[str, str] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def check_format_fields(self) -> Self:
        v2_vals = (self.id, self.version, self.author, self.license, self.compatibility)
        if self.format_version == 1 and (
            any(v is not None for v in v2_vals) or self.visibility != "private"
        ):
            raise ValueError("Format 1 manifests cannot carry format 2 package metadata")
        return self

    def expected_names(self) -> frozenset[str]:
        return frozenset(
            (RECIPE_FILE, SCHEMA_FILE, EXPECTED_FILE, SAMPLE_FILES[self.representation])
        )


def canonical_json(value: Any) -> bytes:
    """Deterministic bytes: sorted keys, two-space indent, LF, one trailing newline."""
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
