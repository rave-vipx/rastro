"""Validation messages that never echo source values (P-005, SEC-005).

Only Pydantic's built-in error types qualify, and only those whose message
template is fixed or interpolates schema constraints (a pattern, a bound, the
expected literals). Types whose message carries input-derived text, such as the
discriminator tag in ``union_tag_invalid`` or a parser's ``error`` detail, and
custom validator errors, get no message.
"""

from __future__ import annotations

from typing import get_args

from pydantic_core import PydanticKnownError
from pydantic_core.core_schema import ErrorType

# Context keys that come from the schema, never from the validated input.
SCHEMA_CONTEXT = frozenset(
    {
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_length",
        "max_length",
        "pattern",
        "expected",
        "max_digits",
        "decimal_places",
        "whole_digits",
        "expected_schemes",
        "expected_version",
        "class_name",
        "expected_plural",
    }
)
_SCHEMA_CONTEXT_TYPES = frozenset(
    {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "multiple_of",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "enum",
        "literal_error",
        "bytes_too_short",
        "bytes_too_long",
        "url_too_long",
        "url_scheme",
        "decimal_max_digits",
        "decimal_max_places",
        "decimal_whole_digits",
        "uuid_version",
        "model_type",
        "dataclass_type",
        "dataclass_exact_type",
        "named_tuple_type",
    }
)


def _fixed(kind: str) -> bool:
    """A type that needs no context has a template with nothing to interpolate."""
    try:
        PydanticKnownError(kind)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    return True


SAFE_ERROR_TYPES = frozenset(
    kind for kind in get_args(ErrorType) if _fixed(kind) or kind in _SCHEMA_CONTEXT_TYPES
)


def safe_message(kind: str, message: str) -> str | None:
    """Pydantic's own message for an allow-listed error type; otherwise None."""
    return f"{kind}: {message}"[:512] if kind in SAFE_ERROR_TYPES else None
