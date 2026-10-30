"""Bounded JSON response parsing and discovery with RFC 6901 pointers (INT-002)."""

from __future__ import annotations

from pydantic import ValidationError

from rastro.domain.json import MISSING, escape_token, resolve_pointer, strict_json
from rastro.domain.models import ConfigurationError, Issue, Limits, Recipe, ResourceLimitError
from rastro.ports.parser import Candidate, ParsedDocument

_DISCOVERY_SAMPLE = 20


def load_json(text: str, limits: Limits) -> object:
    """Decode strict JSON within byte, depth and node budgets.

    Malformed JSON, duplicate keys and non-finite constants raise ValueError.
    """
    if len(text.encode()) > limits.max_json_bytes:
        raise ResourceLimitError("JSON input exceeds max_json_bytes")
    try:
        payload = strict_json(text)
    except RecursionError:
        raise ResourceLimitError("JSON nesting exceeds max_json_depth") from None
    nodes = 0
    pending: list[tuple[object, int]] = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > limits.max_json_nodes:
            raise ResourceLimitError("JSON node count exceeds max_json_nodes")
        if depth > limits.max_json_depth:
            raise ResourceLimitError("JSON nesting exceeds max_json_depth")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return payload


class JsonParser:
    """Select records by `record_path`, then resolve field pointers relative to each record."""

    def parse(self, html: str, recipe: Recipe, limits: Limits) -> ParsedDocument:
        if recipe.strategy != "json" or recipe.record_path is None:
            raise ConfigurationError("JSON documents require a json recipe")
        try:
            payload = load_json(html, limits)
        except ValueError:
            return ParsedDocument((), (Issue(code="invalid_json"),))
        target = resolve_pointer(payload, recipe.record_path)
        if target is MISSING:
            return ParsedDocument((), (Issue(code="record_path_not_found"),))
        if isinstance(target, dict):
            records: list[tuple[object, str]] = [(target, recipe.record_path)]
        elif isinstance(target, list):
            records = [(item, f"{recipe.record_path}/{i}") for i, item in enumerate(target)]
        else:
            return ParsedDocument((), (Issue(code="invalid_record_path"),))
        if len(records) > limits.max_records:
            raise ResourceLimitError("Record count exceeds max_records")
        candidates: list[Candidate] = []
        for index, (item, path) in enumerate(records):
            if not isinstance(item, dict):
                candidates.append(
                    Candidate({}, {}, (Issue(code="invalid_record", record_index=index),))
                )
                continue
            values: dict[str, object] = {}
            locators: dict[str, str] = {}
            for field, pointer in recipe.fields.items():
                value = resolve_pointer(item, pointer)
                if value is not MISSING:
                    values[field] = value
                    locators[field] = f"json#{path}{pointer}"
            candidates.append(Candidate(values, locators))
        return ParsedDocument(tuple(candidates))


class JsonDiscovery:
    """Match exact field names in the root object or arrays of objects; never fetch URLs."""

    def discover(
        self, html: str, fields: tuple[str, ...], limits: Limits, *, max_candidates: int
    ) -> tuple[Recipe, ...]:
        try:
            payload = load_json(html, limits)
        except ValueError:
            return ()
        sets: list[tuple[str, list[dict[str, object]]]] = []
        if isinstance(payload, dict):
            sets.append(("", [payload]))
        # Document-order traversal; load_json already bounded depth and node count.
        pending: list[tuple[object, str]] = [(payload, "")]
        while pending:
            value, pointer = pending.pop()
            children: list[tuple[object, str]] = []
            if isinstance(value, list):
                records = [item for item in value if isinstance(item, dict)]
                if records and len(records) == len(value) and len(value) <= limits.max_records:
                    sets.append((pointer, records))
                children = [(item, f"{pointer}/{i}") for i, item in enumerate(value)]
            elif isinstance(value, dict):
                children = [(item, f"{pointer}/{escape_token(key)}") for key, item in value.items()]
            pending.extend(reversed(children))
        ranked: list[tuple[int, int, Recipe]] = []
        for order, (path, records) in enumerate(sets):
            mapping = _common_paths(records[:_DISCOVERY_SAMPLE], fields)
            if not mapping:
                continue
            try:
                recipe = Recipe(strategy="json", record_path=path, fields=mapping)
            except ValidationError:
                continue
            ranked.append((-len(mapping), order, recipe))
        # Record paths are unique, so ranked recipes are distinct.
        ranked.sort(key=lambda item: item[:2])
        return tuple(recipe for _, _, recipe in ranked[:max_candidates])


def _common_paths(records: list[dict[str, object]], fields: tuple[str, ...]) -> dict[str, str]:
    """Keep a field only where every sampled record that has it agrees on one pointer."""
    agreed: dict[str, str] = {}
    rejected: set[str] = set()
    wanted = set(fields)
    for record in records:
        found: dict[str, list[str]] = {}
        pending: list[tuple[dict[str, object], str]] = [(record, "")]
        while pending:
            current, prefix = pending.pop()
            for key, value in current.items():
                pointer = f"{prefix}/{escape_token(key)}"
                if key in wanted:
                    found.setdefault(key, []).append(pointer)
                if isinstance(value, dict):
                    pending.append((value, pointer))
        for name, pointers in found.items():
            if len(pointers) != 1 or agreed.setdefault(name, pointers[0]) != pointers[0]:
                rejected.add(name)
    return {name: agreed[name] for name in fields if name in agreed and name not in rejected}
