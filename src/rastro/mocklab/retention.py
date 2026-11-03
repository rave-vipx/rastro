"""Decide what a fixture may keep: only values the recipe extracts (MOCK-005).

The plan is built from parser candidates, so it covers every field the recipe and
its fallbacks read, including candidates later rejected by validation. Extracted
values that look like credentials or personal data stop fixture generation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from rastro.domain.json import escape_token
from rastro.domain.models import Limits, Recipe
from rastro.mocklab.detectors import contains_secret, secret_categories
from rastro.mocklab.errors import SanitizationError
from rastro.mocklab.seeds import DeterministicStream
from rastro.ports.parser import Candidate, DocumentParser

_JSONLD_PREFIX = "jsonld["
_JSON_PREFIX = "json#"


@dataclass(frozen=True)
class RetentionPlan:
    """Values and positions that survive sanitization.

    ``texts`` are stripped strings extracted by CSS recipes. ``jsonld_pointers`` maps a
    JSON-LD block index to retained pointers, or is ``None`` when no recipe reads
    JSON-LD. ``json_pointers`` are retained pointers into a JSON response.
    ``selector_text`` is the recipe's own selectors, whose attribute values may stay.
    """

    texts: frozenset[str] = frozenset()
    jsonld_pointers: dict[int, frozenset[str]] | None = None
    json_pointers: frozenset[str] = frozenset()
    selector_text: str = ""


def recipe_variants(recipe: Recipe) -> tuple[Recipe, ...]:
    return (recipe, *recipe.fallbacks)


def build_plan(body: str, recipe: Recipe, parser: DocumentParser, limits: Limits) -> RetentionPlan:
    texts: set[str] = set()
    jsonld: dict[int, set[str]] = {}
    pointers: set[str] = set()
    selectors: list[str] = []
    variants = recipe_variants(recipe)
    for variant in variants:
        selectors.extend(_selectors(variant))
        for candidate in parser.parse(body, variant, limits).candidates:
            _check_values(candidate)
            for field, value in candidate.values.items():
                locator = candidate.locators.get(field, "")
                if variant.strategy == "css":
                    texts.add(str(value))
                elif variant.strategy == "json":
                    pointers.add(locator.removeprefix(_JSON_PREFIX))
                else:
                    index, _, pointer = locator.removeprefix(_JSONLD_PREFIX).partition("]#")
                    jsonld.setdefault(int(index), set()).add(pointer)
    reads_jsonld = any(variant.strategy == "jsonld" for variant in variants)
    return RetentionPlan(
        texts=frozenset(texts),
        jsonld_pointers={index: frozenset(found) for index, found in jsonld.items()}
        if reads_jsonld
        else None,
        json_pointers=frozenset(pointers),
        selector_text="\n".join(selectors),
    )


def _selectors(recipe: Recipe) -> Iterable[str]:
    yield from recipe.fields.values()
    for name in ("record_selector", "item_type", "record_path"):
        value = getattr(recipe, name, None)
        if isinstance(value, str):
            yield value


def _check_values(candidate: Candidate) -> None:
    for field, value in candidate.values.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if contains_secret(text):
            found = ", ".join(secret_categories(text))
            raise SanitizationError(
                f"Extracted field {field!r} looks like sensitive data ({found}); "
                "fixtures never retain it"
            )


def scrub_json(
    value: object,
    retained: frozenset[str],
    stream: DeterministicStream,
    *,
    pointer: str = "",
    keep_keys: frozenset[str] = frozenset(),
) -> object:
    """Replace every JSON leaf outside ``retained`` pointers with a deterministic value.

    Keys survive so record paths and pointers still resolve; keys that look like
    secrets are replaced. Values under ``keep_keys`` (such as ``@type``) survive when
    they contain no sensitive pattern.
    """
    if pointer in retained:
        return value
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, child in value.items():
            name = stream.placeholder(key) if contains_secret(key) else key
            if name in result:
                raise SanitizationError("Scrubbed JSON keys collide")
            child_pointer = f"{pointer}/{escape_token(key)}"
            if key in keep_keys and not contains_secret(json.dumps(child, ensure_ascii=False)):
                result[name] = child
            else:
                result[name] = scrub_json(
                    child, retained, stream, pointer=child_pointer, keep_keys=keep_keys
                )
        return result
    if isinstance(value, list):
        return [
            scrub_json(item, retained, stream, pointer=f"{pointer}/{index}", keep_keys=keep_keys)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return stream.placeholder(value)
    if isinstance(value, bool) or value is None:
        return value
    return stream.integer(0, 999)
