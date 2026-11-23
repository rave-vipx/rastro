"""Declarative extraction recipes and strategy-discriminated unions."""

from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, StrictInt, field_validator, model_validator

from rastro.domain.json import POINTER
from rastro.domain.page_steps import PageSteps
from rastro.domain.recipe_nested import (
    MAX_POINTERS,
    FieldMapping,
    NestedMapping,
    PointerList,
    check_pointer,
    pointer_list,
    scalar_expression,
)
from rastro.domain.recipe_steps import check_recipe_steps
from rastro.domain.recipe_union import StrategyUnion, seal_union
from rastro.domain.sources import ConfigurationError, Contract, Representation

__all__ = [
    "DOCUMENT_PREFIX",
    "EXPAND_PREFIX",
    "MAX_ITEM_TYPES",
    "MAX_POINTERS",
    "CssRecipe",
    "EmbeddedJsonRecipe",
    "FieldMapping",
    "ItemType",
    "JsonLdRecipe",
    "JsonRecipe",
    "NestedMapping",
    "Pagination",
    "PointerList",
    "Recipe",
    "StrategyUnion",
    "pointer_list",
    "scalar_expression",
    "seal_union",
]

DOCUMENT_PREFIX = "document:"  # CSS field evaluated against the page root, not the record
EXPAND_PREFIX = "expand:"  # CSS field whose every match becomes its own record
MAX_ITEM_TYPES = 8  # JSON-LD item_type may list 1..8 accepted types
ItemType = Annotated[str, Field(min_length=1, max_length=256)]


class Pagination(Contract):
    """A bounded next-page expression, interpreted by the recipe strategy."""

    next: str = Field(min_length=1, max_length=1024)
    max_pages: StrictInt = Field(default=5, ge=1, le=50)


class Recipe(StrategyUnion):
    """A declarative definition of field mappings: what to extract, never history.

    ``Recipe(...)`` and ``Recipe.model_validate(...)`` return a ``CssRecipe``,
    ``JsonLdRecipe``, ``EmbeddedJsonRecipe`` or ``JsonRecipe`` selected by ``strategy``.
    """

    strategy: Literal["jsonld", "css", "json", "embedded_json"]
    fields: dict[str, FieldMapping] = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1, le=2_147_483_647)
    require_evidence: bool = False
    fallbacks: tuple[Recipe, ...] = Field(default=(), max_length=3)
    pagination: Pagination | None = Field(default=None, exclude_if=lambda value: value is None)
    # The document kind this recipe reads: JSON responses or HTML pages.
    representation: ClassVar[Representation]

    @model_validator(mode="after")
    def check_mapping(self) -> Self:
        # Fallback variants are typed per representation; only flatness needs checking.
        if any(item.fallbacks or getattr(item, "steps", ()) for item in self.fallbacks):
            raise ValueError("Fallbacks must be flat and cannot carry page steps")
        if any(item.version != self.version for item in self.fallbacks):
            raise ValueError("Fallbacks must be flat and share the recipe version")
        if any(item.pagination is not None for item in self.fallbacks):
            raise ConfigurationError("Fallback recipes cannot declare pagination")
        if self.pagination is not None:
            expression = self.pagination.next
            if expression.startswith((DOCUMENT_PREFIX, EXPAND_PREFIX)):
                raise ValueError("Pagination requires a scalar page expression")
            self.check_expression(expression)
        self.check_fields(self.fields)
        return self

    def check_fields(self, fields: dict[str, FieldMapping]) -> None:
        for name, mapping in fields.items():
            if not name or len(name) > 256:
                raise ValueError("Field names must be nonempty and bounded")
            if isinstance(mapping, NestedMapping):
                self.check_each(mapping.each)
                expressions = (
                    tuple(mapping.members.values()) if mapping.members else (mapping.value,)
                )
                if any(not key or len(key) > 256 for key in mapping.members):
                    raise ValueError("Member names must be nonempty and bounded")
            else:
                expressions = (mapping,) if isinstance(mapping, str) else mapping
                if isinstance(mapping, tuple) and self.strategy != "jsonld":
                    raise ValueError("Fallback pointers require the JSON-LD strategy")
            for expression in expressions:
                if not expression or len(expression) > 1024:
                    raise ValueError("Field expressions must be nonempty and bounded")
                self.check_expression(expression)

    def check_each(self, expression: str) -> None:
        """Validate an absolute array pointer selecting one level of items."""
        check_pointer(expression)
        if not expression.startswith("/") or expression.split("/").count("*") != 1:
            raise ValueError("Nested JSON mappings require exactly one wildcard segment")

    @staticmethod
    def check_expression(expression: str) -> None:
        """Raise ValueError if a field expression is invalid for this strategy."""


def _check_record_path(value: str) -> str:
    if POINTER.fullmatch(value) is None:
        raise ValueError("JSON recipes require record_path as a JSON pointer")
    return value


class CssRecipe(Recipe):
    """Records at ``record_selector``; fields end in ::text or ::attr(name); steps run first."""

    strategy: Literal["css"] = "css"
    fallbacks: tuple[HtmlRecipe, ...] = Field(default=(), max_length=3)
    record_selector: str = Field(min_length=1, max_length=1024)
    steps: PageSteps = Field(
        default=(),
        exclude_if=lambda value: not value,
        description="Browser actions (click, type, select, scroll, wait, follow) run before "
        "extraction; data only, never code.",
    )
    representation: ClassVar[Representation] = "html"

    @model_validator(mode="after")
    def check_expand(self) -> Self:
        check_recipe_steps(self.steps)
        if (
            sum(
                isinstance(value, str) and value.startswith(EXPAND_PREFIX)
                for value in self.fields.values()
            )
            > 1
        ):
            raise ValueError("At most one field can use the expand: prefix")
        return self

    def check_each(self, expression: str) -> None:
        if (
            not expression.strip()
            or "::" in expression
            or expression.startswith((DOCUMENT_PREFIX, EXPAND_PREFIX))
        ):
            raise ValueError("Nested CSS mappings require a plain scope selector")

    @staticmethod
    def check_expression(expression: str) -> None:
        if expression.startswith(EXPAND_PREFIX):
            expression = expression.removeprefix(EXPAND_PREFIX)
            if expression.startswith(DOCUMENT_PREFIX):
                raise ValueError("expand: cannot be combined with document:")
        expression = expression.removeprefix(DOCUMENT_PREFIX)
        if not re.search(r"::(?:text|attr\([^)]+\))$", expression):
            raise ValueError("CSS field mappings must end in ::text or ::attr(name)")


class JsonLdRecipe(Recipe):
    """Map JSON-LD objects, optionally of one or several ``item_type`` values, with pointers.

    ``item_type`` is one type or 1..8 types; an object matches when any of its ``@type``
    values is listed. A field maps to one pointer or to 1..4 pointers; the first that
    resolves wins.
    """

    strategy: Literal["jsonld"] = "jsonld"
    fallbacks: tuple[HtmlRecipe, ...] = Field(default=(), max_length=3)
    item_type: (
        ItemType
        | Annotated[tuple[ItemType, ...], Field(min_length=1, max_length=MAX_ITEM_TYPES)]
        | None
    ) = None
    representation: ClassVar[Representation] = "html"
    check_expression = staticmethod(check_pointer)

    @property
    def item_types(self) -> frozenset[str]:
        """The accepted ``@type`` values; empty when any object matches."""
        if self.item_type is None:
            return frozenset()
        return frozenset({self.item_type} if isinstance(self.item_type, str) else self.item_type)


class JsonRecipe(Recipe):
    """Select JSON records at ``record_path``; fields are pointers relative to each record."""

    strategy: Literal["json"] = "json"
    fallbacks: tuple[JsonRecipe, ...] = Field(default=(), max_length=3)
    record_path: str = Field(max_length=1024)
    representation: ClassVar[Representation] = "json"
    check_expression = staticmethod(check_pointer)

    check_record_path = field_validator("record_path")(_check_record_path)


class EmbeddedJsonRecipe(Recipe):
    """Read JSON embedded in HTML: ``source`` selects one node, then ``record_path`` and fields
    work as in ``JsonRecipe``.

    ``source`` is a CSS selector ending in ``::attr(name)`` (``input[name=GXState]::attr(value)``)
    or ``::text`` (``script#__NEXT_DATA__::text``); it must match exactly one node.
    """

    strategy: Literal["embedded_json"] = "embedded_json"
    fallbacks: tuple[HtmlRecipe, ...] = Field(default=(), max_length=3)
    source: str = Field(min_length=1, max_length=1024)
    record_path: str = Field(max_length=1024)
    representation: ClassVar[Representation] = "html"
    check_expression = staticmethod(check_pointer)
    check_record_path = field_validator("record_path")(_check_record_path)

    @field_validator("source")
    @classmethod
    def check_source(cls, value: str) -> str:
        CssRecipe.check_expression(value)
        if value.startswith((EXPAND_PREFIX, DOCUMENT_PREFIX)):
            raise ValueError("source is a plain CSS selector")
        return value

    @model_validator(mode="after")
    def check_no_pagination(self) -> Self:
        if self.pagination is not None:
            raise ConfigurationError("Embedded JSON recipes cannot declare pagination")
        return self


type HtmlRecipe = Annotated[
    CssRecipe | JsonLdRecipe | EmbeddedJsonRecipe, Field(discriminator="strategy")
]
for _variant in (CssRecipe, JsonLdRecipe, EmbeddedJsonRecipe, JsonRecipe):
    _variant.model_rebuild()
seal_union(Recipe, CssRecipe, JsonLdRecipe, JsonRecipe, EmbeddedJsonRecipe)
