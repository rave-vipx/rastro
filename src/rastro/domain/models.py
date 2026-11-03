"""Public extraction contracts; independent of parsers, storage, and transports."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Self, cast, get_args
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from rastro.domain.json import POINTER

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


_SELECTORS = frozenset({"item_type", "record_selector", "record_path"})


class StrategyUnion(Contract):
    """A family discriminated by ``strategy``. Its base constructs and validates variants.

    ``Base(...)``, ``Base.model_validate(...)`` and fields annotated with the base
    select the variant named by ``strategy``; each variant rejects other strategies' keys.
    """

    _variants: ClassVar[dict[str, type[StrategyUnion]]] = {}

    def __new__(cls, *args: Any, **data: Any) -> Self:
        variants = cls.__dict__.get("_variants")
        if not variants:
            return super().__new__(cls)
        strategy = data.get("strategy")
        variant = variants.get(strategy) if isinstance(strategy, str) else None
        # An unknown or missing strategy raises the union's discriminator error.
        selected = variant or type(cls.__pydantic_validator__.validate_python(data))
        return cast(Self, super().__new__(selected))

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[BaseModel], handler: GetCoreSchemaHandler, /
    ) -> CoreSchema:
        variants = cls.__dict__.get("_variants")
        if not variants:
            return handler(source)
        return core_schema.tagged_union_schema(
            {tag: handler.generate_schema(variant) for tag, variant in variants.items()},
            discriminator="strategy",
        )

    @model_validator(mode="before")
    @classmethod
    def omit_null_selectors(cls, data: Any) -> Any:
        # Serializations before the union wrote every strategy's selector, often as null.
        if isinstance(data, dict):
            return {
                name: value
                for name, value in data.items()
                if value is not None or name not in _SELECTORS or name in cls.model_fields
            }
        return data


def seal_union(base: type[StrategyUnion], *variants: type[StrategyUnion]) -> None:
    """Make ``base`` construct and validate as the discriminated union of ``variants``."""
    base._variants = {
        get_args(variant.model_fields["strategy"].annotation)[0]: variant for variant in variants
    }
    base.model_rebuild(force=True)


class Recipe(StrategyUnion):
    """A declarative definition of scalar field mappings: what to extract, never history.

    ``Recipe(...)`` and ``Recipe.model_validate(...)`` return a ``CssRecipe``,
    ``JsonLdRecipe`` or ``JsonRecipe`` selected by ``strategy``.
    """

    strategy: Literal["jsonld", "css", "json"]
    fields: dict[str, str] = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    require_evidence: bool = False
    fallbacks: tuple[Recipe, ...] = Field(default=(), max_length=3)
    # The document kind this recipe reads: JSON responses or HTML pages.
    representation: ClassVar[Representation]

    @model_validator(mode="after")
    def check_mapping(self) -> Self:
        # Fallback variants are typed per representation; only flatness needs checking.
        if any(item.fallbacks or item.version != self.version for item in self.fallbacks):
            raise ValueError("Fallbacks must be flat and share the recipe version")
        for name, expression in self.fields.items():
            if not name or len(name) > 256 or not expression or len(expression) > 1024:
                raise ValueError("Field names and expressions must be nonempty and bounded")
            self.check_expression(expression)
        return self

    @staticmethod
    def check_expression(expression: str) -> None:
        """Raise ValueError if a field expression is invalid for this strategy."""


def _check_pointer(expression: str) -> None:
    if not expression.startswith("/") or POINTER.fullmatch(expression) is None:
        raise ValueError("JSON mappings must be valid absolute JSON pointers")


class CssRecipe(Recipe):
    """Select HTML records with ``record_selector``; fields end in ::text or ::attr(name)."""

    strategy: Literal["css"] = "css"
    fallbacks: tuple[HtmlRecipe, ...] = Field(default=(), max_length=3)
    record_selector: str = Field(min_length=1, max_length=1024)
    representation: ClassVar[Representation] = "html"

    @staticmethod
    def check_expression(expression: str) -> None:
        if not re.search(r"::(?:text|attr\([^)]+\))$", expression):
            raise ValueError("CSS field mappings must end in ::text or ::attr(name)")


class JsonLdRecipe(Recipe):
    """Map JSON-LD objects, optionally of one ``item_type``, with absolute JSON pointers."""

    strategy: Literal["jsonld"] = "jsonld"
    fallbacks: tuple[HtmlRecipe, ...] = Field(default=(), max_length=3)
    item_type: str | None = Field(default=None, min_length=1, max_length=256)
    representation: ClassVar[Representation] = "html"
    check_expression = staticmethod(_check_pointer)


class JsonRecipe(Recipe):
    """Select JSON records at ``record_path``; fields are pointers relative to each record."""

    strategy: Literal["json"] = "json"
    fallbacks: tuple[JsonRecipe, ...] = Field(default=(), max_length=3)
    record_path: str = Field(max_length=1024)
    representation: ClassVar[Representation] = "json"
    check_expression = staticmethod(_check_pointer)

    @field_validator("record_path")
    @classmethod
    def check_record_path(cls, value: str) -> str:
        if POINTER.fullmatch(value) is None:
            raise ValueError("JSON recipes require record_path as a JSON pointer")
        return value


type HtmlRecipe = Annotated[CssRecipe | JsonLdRecipe, Field(discriminator="strategy")]
for _variant in (CssRecipe, JsonLdRecipe, JsonRecipe):
    _variant.model_rebuild()
seal_union(Recipe, CssRecipe, JsonLdRecipe, JsonRecipe)


class Limits(Contract):
    max_html_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_records: int = Field(default=1_000, ge=1, le=10_000)
    max_jsonld_blocks: int = Field(default=100, ge=1, le=1_000)
    max_json_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_json_depth: int = Field(default=32, ge=1, le=256)
    max_json_nodes: int = Field(default=100_000, ge=1, le=1_000_000)

    def max_input_bytes(self, representation: Representation) -> int:
        return self.max_json_bytes if representation == "json" else self.max_html_bytes


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
    method: Literal["jsonld", "css", "json"]
    locator: str
    recipe_version: int


class ExtractionRecord[T: BaseModel](Contract):
    data: T
    evidence: dict[str, Evidence]


class Metrics(Contract):
    inference_calls: int = 0
    inference_seconds: float = 0
    storage_seconds: float = 0
    acquisition_method: Literal["provided_html", "provided_json", "http"] = "provided_html"
    egress: str = Field(default="direct", pattern=EGRESS_NAME)
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
    origin: Literal["explicit", "memory", "discovery", "model"] = "explicit"
    index: int
    status: Outcome


class ExtractionResult[T: BaseModel](Contract):
    learned_recipe: Recipe | None = None
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
