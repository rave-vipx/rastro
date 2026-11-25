"""The shape of the JSON a recipe reads, per field: what drift watches for JSON sources.

A site's JSON changes shape less often than its markup, and when it does (a number becomes
an object, a list of strings becomes a list of objects) the values stop meaning the same
thing even though every pointer still resolves. Each field gets a digest of the value types
it saw across records; values themselves never enter it.
"""

from __future__ import annotations

import hashlib

from rastro.domain.json import MISSING, resolve_pointer, strict_json
from rastro.domain.models import Limits, Recipe
from rastro.domain.recipes import JsonRecipe, NestedMapping, scalar_expression

__all__ = ["json_shapes"]

_MAX_RECORDS = 200
_MAX_DEPTH = 4
_MAX_KEYS = 64
_MAX_ITEMS = 50


def json_shapes(document: str, recipe: Recipe, limits: Limits) -> dict[str, str]:
    """A SHA-256 digest of each scalar field's value shape; empty when it cannot be read."""
    if not isinstance(recipe, JsonRecipe) or len(document.encode()) > limits.max_json_bytes:
        return {}
    try:
        payload = strict_json(document, repair_text=True)
    except (ValueError, RecursionError):
        return {}
    target = resolve_pointer(payload, recipe.record_path)
    records = target if isinstance(target, list) else [target] if isinstance(target, dict) else []
    sample = [item for item in records[:_MAX_RECORDS] if isinstance(item, dict)]
    if not sample:
        return {}
    shapes = {}
    for name, mapping in recipe.fields.items():
        if isinstance(mapping, NestedMapping):
            continue  # nested members are watched by their coverage
        pointer = scalar_expression(mapping)
        seen = sorted({_shape(resolve_pointer(item, pointer), 0) for item in sample})
        shapes[name] = hashlib.sha256("|".join(seen).encode()).hexdigest()
    return shapes


def _shape(value: object, depth: int) -> str:
    if value is MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if depth >= _MAX_DEPTH:
        return "deep"
    if isinstance(value, list):
        items = sorted({_shape(item, depth + 1) for item in value[:_MAX_ITEMS]})
        return "[" + "|".join(items) + "]"
    if isinstance(value, dict):
        keys = sorted(value)[:_MAX_KEYS]
        return "{" + ",".join(f"{key}:{_shape(value[key], depth + 1)}" for key in keys) + "}"
    return "other"
