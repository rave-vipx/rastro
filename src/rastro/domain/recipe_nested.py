"""Nested field mappings and the helpers that read field mappings."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from rastro.domain.json import POINTER, RELATIVE_POINTER
from rastro.domain.sources import ConfigurationError, Contract

MAX_POINTERS = 4  # JSON-LD fields may list 1..4 fallback pointers
PointerList = Annotated[tuple[str, ...], Field(min_length=1, max_length=MAX_POINTERS)]


class NestedMapping(Contract):
    """Select items, then read either scalar values or named scalar members."""

    each: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "Relative CSS scope selector or absolute JSON pointer with one wildcard segment."
        ),
    )
    members: dict[str, str] = Field(
        default_factory=dict,
        max_length=32,
        description="Named scalar expressions relative to each item; excludes value.",
    )
    value: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="One scalar expression relative to each item; excludes members.",
    )

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if bool(self.members) == (self.value is not None):
            raise ValueError("Nested mappings require exactly one of members or value")
        return self


type FieldMapping = str | PointerList | NestedMapping


def scalar_expression(mapping: FieldMapping) -> str:
    """Return a single-expression mapping; nested and fallback mappings are not scalar."""
    if not isinstance(mapping, str):
        raise ConfigurationError("This operation supports single-expression field mappings only")
    return mapping


def pointer_list(mapping: FieldMapping) -> tuple[str, ...]:
    """Return the ordered fallback pointers of a non-nested mapping."""
    if isinstance(mapping, NestedMapping):
        raise ConfigurationError("Nested field mappings are not extracted by this strategy yet")
    return (mapping,) if isinstance(mapping, str) else mapping


def check_pointer(expression: str) -> None:
    if not (
        (expression.startswith("/") and POINTER.fullmatch(expression))
        or RELATIVE_POINTER.fullmatch(expression)
    ):
        raise ValueError("JSON mappings must be valid absolute or relative JSON pointers")
