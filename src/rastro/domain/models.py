"""Public extraction contracts; independent of parsers, storage, and transports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(Contract):
    """Origin metadata for supplied HTML. Constructing a source never fetches its URL."""

    url: str = Field(min_length=1, max_length=8192)
    locale: str = Field(default="und", min_length=2, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)

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


class Recipe(Contract):
    """Declarative scalar field mappings. JSON-LD uses RFC 6901 JSON pointers."""

    strategy: Literal["jsonld", "css"]
    fields: dict[str, str] = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    fallbacks: tuple[Recipe, ...] = Field(default=(), max_length=3)
    item_type: str | None = Field(default=None, min_length=1, max_length=256)
    record_selector: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def check_mapping(self) -> Self:
        if any(item.fallbacks or item.version != self.version for item in self.fallbacks):
            raise ValueError("Fallbacks must be flat and share the recipe version")
        for name, expression in self.fields.items():
            if not name or len(name) > 256 or not expression or len(expression) > 1024:
                raise ValueError("Field names and expressions must be nonempty and bounded")
            if self.strategy == "jsonld" and (
                not expression.startswith("/") or re.search(r"~(?![01])", expression)
            ):
                raise ValueError("JSON-LD mappings must be valid absolute JSON pointers")
            if self.strategy == "css" and not re.search(r"::(?:text|attr\([^)]+\))$", expression):
                raise ValueError("CSS field mappings must end in ::text or ::attr(name)")
        if self.strategy == "css" and (not self.record_selector or self.item_type):
            raise ValueError("CSS recipes require record_selector and cannot set item_type")
        if self.strategy == "jsonld" and self.record_selector:
            raise ValueError("JSON-LD recipes cannot set record_selector")
        return self


class Limits(Contract):
    max_html_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_records: int = Field(default=1_000, ge=1, le=10_000)
    max_jsonld_blocks: int = Field(default=100, ge=1, le=1_000)


class Outcome(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    MISSING_DATA = "missing_data"
    INVALID_DATA = "invalid_data"
    ACCESS_RESTRICTION = "access_restriction"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Issue(Contract):
    """Safe diagnostics: no input values or user-supplied validation messages."""

    code: str
    record_index: int | None = None
    field: str | None = None


class Evidence(Contract):
    source_url: str
    observed_at: datetime
    method: Literal["jsonld", "css"]
    locator: str
    recipe_version: int


class ExtractionRecord[T: BaseModel](Contract):
    data: T
    evidence: dict[str, Evidence]


class Metrics(Contract):
    storage_seconds: float = 0
    acquisition_method: Literal["provided_html", "http"] = "provided_html"
    acquisition_seconds: float = 0
    network_seconds: float = 0
    request_count: int = 0
    retry_count: int = 0
    redirect_count: int = 0
    bytes_received: int = 0
    input_bytes: int
    candidates: int
    accepted: int
    fields_validated: int = 0
    rejected: int
    extraction_seconds: float
    validation_seconds: float
    total_seconds: float


class RecipeAttempt(Contract):
    index: int
    status: Outcome


class ExtractionResult[T: BaseModel](Contract):
    recipe_version: int | None = None
    recipe_reused: bool = False
    recipe_attempts: tuple[RecipeAttempt, ...] = ()
    requested_url: str | None = None
    http_status: int | None = None
    retry_after_seconds: float | None = None
    job_id: str
    status: Outcome
    source_url: str
    locale: str
    timezone: str
    schema_fingerprint: str
    started_at: datetime
    completed_at: datetime
    records: tuple[ExtractionRecord[T], ...]
    issues: tuple[Issue, ...]
    metrics: Metrics


class RastroError(Exception):
    """Base class for explicit configuration and resource-limit errors."""


class ConfigurationError(RastroError):
    """A recipe cannot be applied to the requested schema or parser."""


class ResourceLimitError(RastroError):
    """The input exceeds a configured resource budget; no truncated success is returned."""


def schema_fingerprint(schema: type[BaseModel]) -> str:
    """Stable identity of the JSON Schema, not a hash of custom Python validator code."""
    canonical = json.dumps(schema.model_json_schema(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
