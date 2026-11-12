"""Source origins, resource budgets, schema fingerprints, and domain errors."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from weakref import ReferenceType, WeakKeyDictionary, ref

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

LANGUAGE_TAG = r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*"
type Representation = Literal["html", "json"]
"""The document kind a recipe reads and an acquisition requests."""
EGRESS_NAME = r"^[a-z][a-z0-9_-]{0,63}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(Contract):
    """Origin and explicit regional context. Constructing a source never fetches its URL."""

    url: str = Field(min_length=1, max_length=8192)
    locale: str = Field(default="und", min_length=2, max_length=64)
    languages: tuple[str, ...] = Field(default=(), max_length=20)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    egress: str = Field(default="direct", pattern=EGRESS_NAME)

    @model_validator(mode="before")
    @classmethod
    def reject_geolocation(cls, data: Any) -> Any:
        # Egress names a network origin; it never implies a browser-reported location.
        if isinstance(data, dict) and "geolocation" in data:
            raise ValueError("Browser geolocation is unavailable without a browser adapter")
        return data

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(language) > 64 or re.fullmatch(LANGUAGE_TAG, language) is None for language in value
        ):
            raise ValueError("Language preferences must contain valid bounded language tags")
        return value

    @field_validator("url")
    @classmethod
    def reject_credentials(cls, value: str) -> str:
        parsed = AnyHttpUrl(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Source URLs must not contain credentials")
        return str(parsed)

    @property
    def evidence_url(self) -> str:
        """Exclude query strings and fragments from provenance and diagnostics."""
        parts = urlsplit(str(self.url))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class Limits(Contract):
    max_html_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_records: int = Field(default=1_000, ge=1, le=10_000)
    max_jsonld_blocks: int = Field(default=100, ge=1, le=1_000)
    max_json_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_json_depth: int = Field(default=32, ge=1, le=256)
    max_json_nodes: int = Field(default=100_000, ge=1, le=1_000_000)

    def max_input_bytes(self, representation: Representation) -> int:
        return self.max_json_bytes if representation == "json" else self.max_html_bytes


class RastroError(Exception):
    """Base class for explicit configuration and resource-limit errors.

    ``location`` optionally names the configuration key at fault, such as
    ``dataset.match_fields``, and ``hint`` a next step; neither contains input values.
    """

    def __init__(self, *args: object, location: str | None = None, hint: str | None = None) -> None:
        super().__init__(*args)
        self.location = location
        self.hint = hint


class LocatedValueError(ValueError):
    """A validator failure that names its configuration key for machine-readable errors."""

    def __init__(self, message: str, *, location: str) -> None:
        super().__init__(message)
        self.location = location


class ConfigurationError(RastroError):
    """A recipe cannot be applied to the requested schema or parser."""


class ResourceLimitError(RastroError):
    """The input exceeds a configured resource budget; no truncated success is returned."""


_SCHEMA_FINGERPRINTS: WeakKeyDictionary[type[BaseModel], tuple[ReferenceType[Any], str]] = (
    WeakKeyDictionary()
)


def schema_fingerprint(schema: type[BaseModel]) -> str:
    """Stable identity of the JSON Schema, not a hash of custom Python validator code."""
    cached = _SCHEMA_FINGERPRINTS.get(schema)
    token = schema.__dict__.get("__signature__")
    if (
        cached is not None
        and token is not None
        and schema.__pydantic_complete__
        and cached[0]() is token
    ):
        return cached[1]
    canonical = json.dumps(schema.model_json_schema(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    _SCHEMA_FINGERPRINTS[schema] = (ref(schema.__dict__["__signature__"]), digest)
    return digest
