"""Declarative extraction recipes and strategy-discriminated unions."""

from __future__ import annotations

import re
from typing import Annotated, Any, ClassVar, Literal, Self, cast, get_args

from pydantic import (
    BaseModel,
    Field,
    GetCoreSchemaHandler,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from rastro.domain.json import POINTER
from rastro.domain.sources import Contract, Representation

DOCUMENT_PREFIX = "document:"  # CSS field evaluated against the page root, not the record
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
        expression = expression.removeprefix(DOCUMENT_PREFIX)
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
