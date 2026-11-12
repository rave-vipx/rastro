"""Example-driven recipe suggestions: notes, results and example matching (INT-002, DISC-003).

A suggestion is a deterministic proposal: a CSS recipe built from a saved page and a few
example values, plus the preview of running it through the normal extraction pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urljoin

from pydantic import BaseModel, Field

from rastro.domain.job_fields import JobFieldType
from rastro.domain.models import ConfigurationError, Contract, CssRecipe, ExtractionResult

MAX_EXAMPLES = 20
MAX_EXAMPLE_CHARS = 1_000
type Example = Mapping[str, str]


class SuggestionError(ConfigurationError):
    """No verified recipe could be built from the page and the examples."""


class FieldConfidence(Contract):
    """How one field was mapped. Notes are Rastro's own text, never page content."""

    field: str
    method: Literal["example", "microdata", "unmapped"]
    selector: str | None = None
    examples_matched: int = Field(default=0, ge=0)
    examples_total: int = Field(default=0, ge=0)
    records_with_value: int = Field(default=0, ge=0)
    ambiguous: bool = False
    note: str = ""


class Suggestion[T: BaseModel](Contract):
    """A verified recipe, its preview on the same page and per-field notes."""

    recipe: CssRecipe
    preview: ExtractionResult[T]
    confidence: dict[str, FieldConfidence]


def normalize_text(value: str) -> str:
    """Collapse whitespace, including NBSP, so page text and examples compare fairly."""
    return " ".join(value.replace("\xa0", " ").split())


def check_examples(examples: Sequence[Example]) -> tuple[dict[str, str], ...]:
    """Validate and normalize examples; reject empty, oversized or non-string input."""
    if not 1 <= len(examples) <= MAX_EXAMPLES:
        raise ConfigurationError(f"Give between 1 and {MAX_EXAMPLES} example records")
    checked: list[dict[str, str]] = []
    for position, example in enumerate(examples, 1):
        if not isinstance(example, Mapping) or not example:
            raise ConfigurationError(f"Example {position} must map field names to values")
        record: dict[str, str] = {}
        for name, value in example.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ConfigurationError(f"Example {position} values must be strings")
            text = normalize_text(value)
            if not text or len(text) > MAX_EXAMPLE_CHARS:
                raise ConfigurationError(
                    f"Example {position} field {name!r} must be 1 to {MAX_EXAMPLE_CHARS} characters"
                )
            record[name] = text
        checked.append(record)
    return tuple(checked)


def value_matches(example: str, value: object, base_url: str | None = None) -> bool:
    """True when an extracted value reproduces an example value.

    Exact text, the example contained in the value, equal decimals, or example and value
    resolving to the same URL against ``base_url`` all count.
    """
    if value is None:
        return False
    text = normalize_text(str(value))
    if text == example or example in text:
        return True
    if base_url and urljoin(base_url, example) == urljoin(base_url, text):
        return True
    try:
        return Decimal(example) == Decimal(text)
    except (InvalidOperation, ValueError):
        return False


_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]{0,17})")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_KINDS: tuple[JobFieldType, ...] = ("integer", "decimal", "date", "url")


def infer_field_type(values: Sequence[str]) -> JobFieldType:
    """Conservative type for example values: every value must fit, otherwise ``string``.

    Integers mixed with decimals are ``decimal``. Leading zeros, thousands separators,
    currency symbols and relative URLs stay strings.
    """

    def fits(kind: JobFieldType, value: str) -> bool:
        if kind == "integer":
            return _INTEGER.fullmatch(value) is not None
        if kind == "decimal":
            return _DECIMAL.fullmatch(value) is not None
        if kind == "date":
            return _ISO_DATE.fullmatch(value) is not None and _is_date(value)
        return value.startswith(("https://", "http://")) and " " not in value

    for kind in _KINDS:
        if values and all(fits(kind, value) for value in values):
            return kind
    return "string"


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
