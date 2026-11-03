"""Scrub JSON so only retained values and the keys that reach them survive (MOCK-005).

Values outside retained pointers become placeholders or small integers. Keys survive
only on the path to a retained value or a structural pointer (such as a JSON
recipe's ``record_path``); every other key becomes a placeholder. In JSON-LD,
``@graph`` and ``@type`` keys stay so documents are still traversed and filtered,
but an ``@type`` value stays only when the recipe names it (its ``item_type``).
Nesting is bounded so hostile input fails explicitly instead of overflowing.
"""

from __future__ import annotations

from rastro.domain.json import escape_token
from rastro.mocklab.errors import SanitizationError
from rastro.mocklab.seeds import DeterministicStream

MAX_DEPTH = 256
_LINKED_DATA_KEYS = frozenset({"@graph", "@type"})
_KEY_ATTEMPTS = 16


def pointer_prefixes(pointers: frozenset[str]) -> frozenset[str]:
    """Every pointer and each of its ancestors, excluding the root."""
    prefixes: set[str] = set()
    for pointer in pointers:
        tokens = pointer.split("/")[1:]
        prefixes.update("/" + "/".join(tokens[:end]) for end in range(1, len(tokens) + 1))
    return frozenset(prefixes)


class _Scrubber:
    def __init__(
        self,
        retained: frozenset[str],
        stream: DeterministicStream,
        structure: frozenset[str],
        keep_types: frozenset[str],
        linked_data: bool,
    ) -> None:
        self.retained = retained
        self.paths = pointer_prefixes(retained | structure)
        self.stream = stream
        self.keep_types = keep_types
        self.linked_data = linked_data

    def value(self, value: object, pointer: str, depth: int) -> object:
        if depth > MAX_DEPTH:
            raise SanitizationError("JSON nesting exceeds the sanitizer depth bound")
        if pointer in self.retained:
            return value
        if isinstance(value, dict):
            return self.mapping(value, pointer, depth)
        if isinstance(value, list):
            return [
                self.value(item, f"{pointer}/{index}", depth + 1)
                for index, item in enumerate(value)
            ]
        if isinstance(value, str):
            return self.stream.placeholder(value)
        if isinstance(value, bool) or value is None:
            return value
        return self.stream.integer(0, 999)

    def mapping(self, value: dict[str, object], pointer: str, depth: int) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, child in value.items():
            child_pointer = f"{pointer}/{escape_token(key)}"
            structural = self.linked_data and key in _LINKED_DATA_KEYS
            kept = structural or child_pointer in self.paths
            name = key if kept else self.placeholder_key(key, value, result)
            if self.linked_data and key == "@type" and child_pointer not in self.retained:
                result[name] = self.types(child, child_pointer, depth + 1)
            else:
                result[name] = self.value(child, child_pointer, depth + 1)
        return result

    def placeholder_key(
        self, key: str, source: dict[str, object], result: dict[str, object]
    ) -> str:
        """A placeholder key unused in the source and output objects."""
        for _ in range(_KEY_ATTEMPTS):
            name = self.stream.placeholder(key) if key.strip() else f"_{self.stream.letters(4)}"
            if name not in result and name not in source:
                return name
            key += "_"
        raise SanitizationError("Scrubbed JSON keys collide")

    def types(self, value: object, pointer: str, depth: int) -> object:
        if isinstance(value, str):
            return value if value in self.keep_types else self.stream.placeholder(value)
        if isinstance(value, list):
            return [self.types(item, f"{pointer}/{i}", depth + 1) for i, item in enumerate(value)]
        return self.value(value, pointer, depth)


def scrub_json(
    value: object,
    retained: frozenset[str],
    stream: DeterministicStream,
    *,
    structure: frozenset[str] = frozenset(),
    keep_types: frozenset[str] = frozenset(),
    linked_data: bool = False,
) -> object:
    """Return a scrubbed copy of ``value``; see the module docstring for what survives."""
    scrubber = _Scrubber(retained, stream, structure, keep_types, linked_data)
    return scrubber.value(value, "", 0)
