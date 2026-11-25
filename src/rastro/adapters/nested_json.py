"""Nested list and object fields read from decoded JSON (JSON, JSON-LD, embedded JSON)."""

from __future__ import annotations

from rastro.domain.json import MISSING, resolve_pointer
from rastro.domain.models import Issue, Limits
from rastro.domain.recipes import FieldMapping, NestedMapping

__all__ = ["extract_json_field"]


def extract_json_field(
    item: object,
    mapping: FieldMapping,
    *,
    field: str,
    limits: Limits,
    record_index: int,
) -> tuple[object, tuple[Issue, ...]]:
    """Read a ``NestedMapping`` from ``item``: the value (``MISSING`` when no item matched)
    and the issues raised on the way. Members that are absent become None."""
    if not isinstance(mapping, NestedMapping):
        raise TypeError("extract_json_field reads nested mappings only")
    elements, parents = _elements(item, mapping.each, limits)
    if not elements:
        return MISSING, ()
    issues: tuple[Issue, ...] = ()
    if len(elements) > limits.max_records:
        elements = elements[: limits.max_records]
        issues = (Issue(code="extraction_limit", record_index=record_index, field=field),)
    if mapping.value is not None:
        return [_read(e, mapping.value, parents, limits) for e in elements], issues
    return [
        {
            name: _read(e, expression, parents, limits)
            for name, expression in mapping.members.items()
        }
        for e in elements
    ], issues


def _read(element: object, expression: str, parents: tuple[object, ...], limits: Limits) -> object:
    value = resolve_pointer(element, expression, parents=parents, max_records=limits.max_json_nodes)
    return None if value is MISSING else value


def _elements(item: object, each: str, limits: Limits) -> tuple[list[object], tuple[object, ...]]:
    """The items ``each`` selects, with the containers above them (root first)."""
    found = resolve_pointer(item, each, max_records=limits.max_json_nodes)
    prefix = each.split("/*", 1)[0]
    container = resolve_pointer(item, prefix) if prefix else item
    if found is MISSING and each.endswith("/*") and isinstance(container, dict):
        # A single object where a list is usual (JSON-LD "offers") is a list of one.
        found, container = [container], [container]
    if not isinstance(found, list):
        return [], ()
    parents: list[object] = [item]
    path = ""
    for token in prefix.split("/")[1:-1]:
        path += f"/{token}"
        parents.append(resolve_pointer(item, path))
    if prefix:
        parents.append(container)
    return found, tuple(parents)
