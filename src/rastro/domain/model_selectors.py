"""CSS selectors declared on Pydantic model fields, turned into a recipe (INT-001, REC-003).

``Annotated[str, css("h3::text")]`` keeps the selector next to the field it fills.
The marker never changes the JSON Schema; it strips text and resolves relative links.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from pydantic import BaseModel, GetCoreSchemaHandler, ValidationError
from pydantic_core import CoreSchema, core_schema

from rastro.domain.job_field_values import relative_url, strip_text
from rastro.domain.recipes import CssRecipe
from rastro.domain.sources import ConfigurationError

WHOLE_DOCUMENT = ":root"  # one record: the page itself


@dataclass(frozen=True, slots=True)
class CssSelector:
    """A field's CSS expression ending in ``::text`` or ``::attr(name)``.

    Before validation, text is stripped of surrounding whitespace (NBSP included).
    Blank text in a field that does not accept ``None`` is a validation error, as in
    jobs; a nullable field keeps ``""``. On URL-typed fields root- or dot-relative
    links resolve against the page URL.
    """

    selector: str

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        schema = handler(source)
        if _accepts_url(schema):
            schema = core_schema.with_info_before_validator_function(relative_url, schema)
        strip = partial(strip_text, required=not _accepts_none(schema))
        return core_schema.no_info_before_validator_function(strip, schema)


def _accepts_none(schema: CoreSchema) -> bool:
    kind = schema.get("type")
    if kind in {"nullable", "none"}:
        return True
    if kind in {"default", "function-wrap", "function-before", "function-after"}:
        return _accepts_none(cast(CoreSchema, schema["schema"]))
    if kind == "union":
        choices = schema["choices"]
        return any(_accepts_none(item[0] if isinstance(item, tuple) else item) for item in choices)
    return False


def _accepts_url(schema: CoreSchema) -> bool:
    kind = schema.get("type")
    if kind in {"url", "multi-host-url"}:
        return True
    if kind in {"nullable", "default", "function-wrap", "function-before", "function-after"}:
        return _accepts_url(cast(CoreSchema, schema["schema"]))
    if kind == "union":
        choices = schema["choices"]
        return any(_accepts_url(item[0] if isinstance(item, tuple) else item) for item in choices)
    return False


def css(selector: str) -> CssSelector:
    """Mark a model field with the CSS expression that fills it."""
    if not isinstance(selector, str) or not selector.strip():
        raise ConfigurationError("rastro.css() needs a nonempty selector string")
    return CssSelector(selector)


def declared_selectors(schema: type[BaseModel]) -> dict[str, str]:
    """Selectors from ``rastro.css`` markers, in field declaration order."""
    found: dict[str, str] = {}
    for name, field in schema.model_fields.items():
        markers = [item for item in field.metadata if isinstance(item, CssSelector)]
        if len(markers) > 1:
            raise ConfigurationError(f"Field {name!r} declares more than one rastro.css selector")
        if markers:
            found[name] = markers[0].selector
    return found


def recipe_from_model(
    schema: type[BaseModel],
    *,
    records: str | None = None,
    fields: Mapping[str, str] | None = None,
) -> CssRecipe | None:
    """Build a CSS recipe from model markers plus ``fields`` overrides; None if no selectors.

    The record selector comes from ``records``, else the model's ``__records__``,
    else the whole document is a single record.
    """
    mapping = {**declared_selectors(schema), **dict(fields or {})}
    if not mapping:
        return None
    unknown = sorted(set(mapping) - set(schema.model_fields))
    if unknown:
        raise ConfigurationError("fields= names must be declared model fields")
    record_selector = records if records is not None else getattr(schema, "__records__", None)
    if record_selector is None:
        record_selector = WHOLE_DOCUMENT
    if not isinstance(record_selector, str):
        raise ConfigurationError("The record selector must be a CSS selector string")
    try:
        return CssRecipe(record_selector=record_selector, fields=mapping)
    except ValidationError:
        raise ConfigurationError(
            "Invalid model selectors: each must end in ::text or ::attr(name)"
        ) from None
