"""Strategy-discriminated unions: a base that constructs and validates its variants."""

from __future__ import annotations

from typing import Any, ClassVar, Self, cast, get_args

from pydantic import BaseModel, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, core_schema

from rastro.domain.sources import Contract

_SELECTORS = frozenset({"item_type", "record_selector", "record_path", "source"})


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
