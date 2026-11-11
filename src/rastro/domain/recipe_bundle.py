"""Portable recipe bundle format: manifest, canonical bytes and typed failures (HUB-001)."""

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import Field

from rastro.domain.memory import RecipeContext
from rastro.domain.models import ConfigurationError, Contract, Representation, Source

BUNDLE_FORMAT = 1
MANIFEST_FILE = "bundle.json"
RECIPE_FILE = "recipe.json"
SCHEMA_FILE = "schema.json"
EXPECTED_FILE = "expected.json"
SAMPLE_FILES: dict[Representation, str] = {"html": "sample.html", "json": "sample.json"}
MAX_METADATA_BYTES = 1_048_576


class RecipeBundleError(ConfigurationError):
    """A recipe bundle cannot be exported or trusted; messages never echo bundle content."""


class BundleIntegrityError(RecipeBundleError):
    """Missing, unexpected, oversized, linked or altered bundle files."""


class BundleVersionError(RecipeBundleError):
    """The bundle declares a format this release does not understand."""


class BundleVerificationError(RecipeBundleError):
    """The recipe does not reproduce the bundled expected records on the bundled sample."""


class BundleManifest(Contract):
    """``bundle.json``: what the bundle is for and the digest of every other file."""

    format_version: Literal[1] = 1
    source: Source
    context: RecipeContext
    representation: Representation
    last_verified: date
    files: dict[str, str] = Field(min_length=4, max_length=4)

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
