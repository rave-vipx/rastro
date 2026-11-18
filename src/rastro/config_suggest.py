"""Suggest the closest valid key when a job file has an unknown one (N18, AGENT-001).

Only key names matching ``[A-Za-z0-9_-]{1,64}`` are echoed; anything else in the job file
stays out of the message (AGENT-003)."""

from __future__ import annotations

import difflib
import re
from functools import cache

from pydantic import ValidationError

from rastro.config_schema import job_entries

_SAFE_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_LISTED = 12


def unknown_key_hint(error: ValidationError) -> str | None:
    """A hint for the first unknown key of a job validation error, or ``None``."""
    if error.title != "Job":
        return None
    items = error.errors(include_url=False, include_input=False, include_context=False)
    loc = next((item["loc"] for item in items if item["type"] == "extra_forbidden"), None)
    if not loc or not isinstance(loc[-1], str):
        return None
    children = _children()
    section = ""
    for segment in loc[:-1]:
        candidate = f"{section}.{segment}" if section else str(segment)
        if isinstance(segment, str) and candidate in children:
            section = candidate
    where = f"[{section}]" if section else "the top level"
    keys = sorted(children.get(section, ()))
    key = loc[-1]
    match = difflib.get_close_matches(key, keys, n=1, cutoff=0.6)
    if not _SAFE_KEY.fullmatch(key):
        return f"Unknown key in {where}; `rastro config` lists every valid key"
    if match:
        return f'Unknown key "{key}" in {where}; did you mean "{match[0]}"?'
    listed = ", ".join(keys[:_MAX_LISTED]) + (", ..." if len(keys) > _MAX_LISTED else "")
    return f'Unknown key "{key}" in {where}; valid keys: {listed}'


@cache
def _children() -> dict[str, frozenset[str]]:
    """Plain section path ("" for the top level) -> its key names, across variants."""
    from rastro import job_json_schema

    found: dict[str, set[str]] = {}
    for entry in job_entries(job_json_schema()):
        plain = entry.path.replace("[]", "").replace(".<name>", "")
        parent, _, name = plain.rpartition(".")
        found.setdefault(parent, set()).add(name)
    return {parent: frozenset(names) for parent, names in found.items()}
