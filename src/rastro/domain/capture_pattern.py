"""A recipe's capture pattern: a bounded regular expression over a response path.

The pattern comes from recipe data, which may be shared, so it is checked like any untrusted
input: short, and compiled and matched by the same linear-time engine as spider path patterns,
so no pattern can backtrack catastrophically. It is matched against a path that never carries a
query.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

from rastro.domain.linear_regex import check_linear_pattern, linear_search

__all__ = [
    "MAX_CAPTURE_PATH",
    "MAX_CAPTURE_PATTERN",
    "CapturePattern",
    "capture_matches",
    "check_capture_pattern",
]

MAX_CAPTURE_PATH = 1024
"""Longer response paths are never captured, so a pattern only ever sees bounded input."""

MAX_CAPTURE_PATTERN = 256
"""The longest ``capture`` pattern a recipe may carry."""


def check_capture_pattern(value: str) -> str:
    """Return ``value`` when it is a safe, valid pattern; raise ValueError otherwise."""
    return check_linear_pattern(value)


type CapturePattern = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_CAPTURE_PATTERN),
    AfterValidator(check_capture_pattern),
]


def capture_matches(pattern: str, path: str) -> bool:
    """Whether ``pattern`` finds a match in ``path`` (a search, not a full match)."""
    return len(path) <= MAX_CAPTURE_PATH and linear_search(pattern, path)
