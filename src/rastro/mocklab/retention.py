"""Decide what a fixture may keep: only values the recipe extracts (MOCK-005).

The plan is built from parser candidates, so it covers every field the recipe and
its fallbacks read, including candidates later rejected by validation. Extracted
values that look like credentials or personal data stop fixture generation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

from rastro.domain.models import Limits, Recipe
from rastro.mocklab.detectors import contains_secret, secret_categories, selector_tokens
from rastro.mocklab.errors import SanitizationError
from rastro.ports.parser import Candidate, DocumentParser

_JSONLD_PREFIX = "jsonld["
_JSON_PREFIX = "json#"


@dataclass(frozen=True)
class RetentionPlan:
    """Values and positions that survive sanitization.

    ``texts`` are stripped strings extracted by CSS recipes. ``jsonld_pointers`` maps a
    JSON-LD block index to retained pointers, or is ``None`` when no recipe reads
    JSON-LD. ``json_pointers`` are retained pointers into a JSON response.
    ``json_structure`` are pointers whose keys must survive, such as ``record_path``.
    ``selector_text`` is the recipe's own selectors; their identifier tokens are the
    only attribute tokens, URL path segments and JSON-LD types that may stay.
    """

    texts: frozenset[str] = frozenset()
    jsonld_pointers: dict[int, frozenset[str]] | None = None
    json_pointers: frozenset[str] = frozenset()
    json_structure: frozenset[str] = frozenset()
    selector_text: str = ""

    @cached_property
    def tokens(self) -> frozenset[str]:
        return selector_tokens(self.selector_text)


def recipe_variants(recipe: Recipe) -> tuple[Recipe, ...]:
    return (recipe, *recipe.fallbacks)


def build_plan(body: str, recipe: Recipe, parser: DocumentParser, limits: Limits) -> RetentionPlan:
    texts: set[str] = set()
    jsonld: dict[int, set[str]] = {}
    pointers: set[str] = set()
    structure: set[str] = set()
    selectors: list[str] = []
    variants = recipe_variants(recipe)
    for variant in variants:
        selectors.extend(_selectors(variant))
        record_path = getattr(variant, "record_path", None)
        if isinstance(record_path, str) and record_path:
            structure.add(record_path)
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
        json_structure=frozenset(structure),
        selector_text="\n".join(selectors),
    )


def _selectors(recipe: Recipe) -> Iterable[str]:
    for mapping in recipe.fields.values():
        yield from (mapping,) if isinstance(mapping, str) else mapping
    for name in ("record_selector", "item_type", "record_path"):
        value = getattr(recipe, name, None)
        if isinstance(value, str):
            yield value


def _check_values(candidate: Candidate) -> None:
    for field, value in candidate.values.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if contains_secret(text, include_opaque=False):
            found = ", ".join(secret_categories(text, include_opaque=False))
            raise SanitizationError(
                f"Extracted field {field!r} looks like sensitive data ({found}); "
                "fixtures never retain it"
            )
