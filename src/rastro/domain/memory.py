"""Recipe identity and persisted validation evidence, independent of storage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from rastro.domain.models import (
    Contract,
    RastroError,
    Recipe,
    Representation,
    Source,
    schema_fingerprint,
)


class RecipeContext(Contract):
    """Caller-managed template, region and custom-validator revision identifiers."""

    template: str = Field(default="page", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
    region: str = Field(default="default", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
    schema_revision: str = Field(default="1", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")


class RecipeKey(Contract):
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    locale: str = Field(min_length=2, max_length=64)
    languages: tuple[str, ...] = Field(default=(), max_length=20)
    timezone: str = Field(min_length=1, max_length=128)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    egress: Literal["direct"] = "direct"
    context: RecipeContext = RecipeContext()
    representation: Representation = "html"

    @classmethod
    def for_source(
        cls,
        source: Source,
        schema: type[BaseModel],
        context: RecipeContext | None = None,
        *,
        representation: Representation = "html",
    ) -> RecipeKey:
        source = Source.model_validate(source.model_dump())
        parts = urlsplit(source.url)
        # Queries affect identity but are never retained; fragments do not affect HTTP content.
        identity = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        return cls(
            source_fingerprint=hashlib.sha256(identity.encode()).hexdigest(),
            schema_fingerprint=schema_fingerprint(schema),
            locale=source.locale,
            languages=source.languages,
            timezone=source.timezone,
            currency=source.currency,
            egress=source.egress,
            context=context or RecipeContext(),
            representation=representation,
        )

    def canonical(self) -> str:
        key = RecipeKey.model_validate(self.model_dump())
        payload = key.model_dump()
        if not key.languages and key.currency is None and key.egress == "direct":
            for field in ("languages", "currency", "egress"):
                payload.pop(field)
        # HTML keys predate structured responses; JSON recipes never share their memory.
        if key.representation == "html":
            payload.pop("representation")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class StoredRecipe(Contract):
    key: RecipeKey
    recipe: Recipe
    selected_index: int = Field(ge=0, le=3)
    created_at: AwareDatetime
    last_success_at: AwareDatetime
    successful_runs: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_history(self) -> Self:
        if self.selected_index > len(self.recipe.fallbacks):
            raise ValueError("Validated alternative does not exist")
        if self.last_success_at < self.created_at:
            raise ValueError("Recipe success precedes creation")
        return self

    @property
    def validated_recipe(self) -> Recipe:
        """Only the alternative that actually passed extraction is eligible for reuse."""
        return (self.recipe, *self.recipe.fallbacks)[self.selected_index]


class RecipeStorageError(RastroError):
    """Persistence failed; messages never include SQL, payloads or filesystem paths."""


class RecipeConflictError(RecipeStorageError):
    """A version is immutable; changed definitions require a new version."""


class RecipeNotFoundError(RastroError):
    """No validated recipe exists for the exact requested context and version."""


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Recipe timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")
