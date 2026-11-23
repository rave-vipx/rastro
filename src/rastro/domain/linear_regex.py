"""Regular expressions from job or recipe data, run on a linear-time engine.

Patterns can arrive in shared recipes and bundles, so they never reach Python's backtracking
``re``: pydantic-core's Rust regex engine compiles and matches them in time linear in the
input. Look-around and backreferences are refused because that engine does not support them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, TypeAdapter, ValidationError
from pydantic_core import SchemaError

__all__ = ["check_linear_pattern", "linear_search"]


@lru_cache(maxsize=128)
def _compiled(pattern: str) -> TypeAdapter[str]:
    return TypeAdapter(Annotated[str, Field(pattern=pattern)])


def check_linear_pattern(value: str) -> str:
    """Return ``value`` when the linear-time engine compiles it; raise ValueError otherwise."""
    try:
        _compiled(value)
    except SchemaError:
        raise ValueError(
            "Invalid or unsupported regular expression pattern "
            "(look-around and backreferences are not supported)"
        ) from None
    return value


def linear_search(pattern: str, text: str) -> bool:
    """Whether ``pattern`` finds a match anywhere in ``text`` (search, not full match)."""
    try:
        _compiled(pattern).validate_python(text)
    except ValidationError:
        return False
    return True
