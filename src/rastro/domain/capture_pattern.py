"""A recipe's capture pattern: a bounded regular expression over a response path.

The pattern comes from recipe data, which may be shared, so it is checked like any untrusted
input: short, valid, and without a quantified group that itself repeats (the shape behind
catastrophic backtracking). It is matched against a path that never carries a query.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

__all__ = ["MAX_CAPTURE_PATH", "CapturePattern", "capture_matches", "check_capture_pattern"]

MAX_CAPTURE_PATH = 1024
"""Longer response paths are never captured, so a pattern only ever sees bounded input."""

# A group containing a quantifier, itself followed by a quantifier: (a+)+, (\d*)*, (x{2,})+.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,?\d*\})\)(?:[+*?]|\{)")


def check_capture_pattern(value: str) -> str:
    """Return ``value`` when it is a safe, valid pattern; raise ValueError otherwise."""
    if _NESTED_QUANTIFIER.search(value):
        raise ValueError("capture patterns cannot repeat a group that already repeats")
    try:
        re.compile(value)
    except re.error:
        raise ValueError("capture must be a valid regular expression") from None
    return value


type CapturePattern = Annotated[
    StrictStr, Field(min_length=1, max_length=256), AfterValidator(check_capture_pattern)
]


def capture_matches(pattern: str, path: str) -> bool:
    """Whether ``pattern`` finds a match in ``path`` (a search, not a full match)."""
    return len(path) <= MAX_CAPTURE_PATH and re.search(pattern, path) is not None
