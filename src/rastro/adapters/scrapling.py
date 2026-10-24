"""Offline parsing through Scrapling. No fetchers, rendering, or adaptive storage."""

from __future__ import annotations

import json
from collections.abc import Iterator

from cssselect import SelectorError
from scrapling.parser import Selector

from rastro.domain.models import ConfigurationError, Issue, Limits, Recipe, ResourceLimitError
from rastro.ports.parser import Candidate, ParsedDocument

_MISSING = object()


def _resolve(value: object, pointer: str) -> object:
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value.get(key, _MISSING)
        elif isinstance(value, list) and key.isascii() and key.isdigit():
            if len(key) > 1 and key.startswith("0"):
                return _MISSING
            # Avoid parsing an unbounded integer from an untrusted pointer.
            if len(key) > 9 or int(key) >= len(value):
                return _MISSING
            value = value[int(key)]
        else:
            return _MISSING
    return value


def _objects(value: object, path: str = "") -> Iterator[tuple[dict[str, object], str]]:
    """Traverse root arrays and @graph containers, never nested field values."""
    pending = [(value, path)]
    while pending:
        current, locator = pending.pop()
        if isinstance(current, list):
            pending.extend(
                (item, f"{locator}/{i}") for i, item in reversed(list(enumerate(current)))
            )
        elif isinstance(current, dict):
            if "@graph" in current:
                pending.append((current["@graph"], f"{locator}/@graph"))
            else:
                yield current, locator


class ScraplingParser:
    """Translate parser output into bounded, declarative candidates."""

    def parse(self, html: str, recipe: Recipe, limits: Limits) -> ParsedDocument:
        if not html.strip():
            return ParsedDocument(())
        page = Selector(html, adaptive=False, huge_tree=False)
        try:
            if recipe.strategy == "css":
                return self._css(page, recipe, limits)
            return self._jsonld(page, recipe, limits)
        except SelectorError:
            raise ConfigurationError("Recipe contains an invalid CSS selector") from None

    def _css(self, page: Selector, recipe: Recipe, limits: Limits) -> ParsedDocument:
        assert recipe.record_selector is not None
        nodes = page.css(recipe.record_selector)
        if len(nodes) > limits.max_records:
            raise ResourceLimitError("Record count exceeds max_records")
        candidates: list[Candidate] = []
        for index, node in enumerate(nodes):
            values: dict[str, object] = {}
            locators: dict[str, str] = {}
            issues: list[Issue] = []
            for field, selector in recipe.fields.items():
                matches = node.css(selector).getall()
                if len(matches) > 1:
                    issues.append(Issue(code="ambiguous_match", record_index=index, field=field))
                elif matches:
                    values[field] = str(matches[0]).strip()
                    locators[field] = f"{recipe.record_selector} [match {index}] >> {selector}"
            candidates.append(Candidate(values, locators, tuple(issues)))
        return ParsedDocument(tuple(candidates))

    def _jsonld(self, page: Selector, recipe: Recipe, limits: Limits) -> ParsedDocument:
        blocks = page.css('script[type="application/ld+json"]::text').getall()
        if len(blocks) > limits.max_jsonld_blocks:
            raise ResourceLimitError("JSON-LD block count exceeds max_jsonld_blocks")
        candidates: list[Candidate] = []
        issues: list[Issue] = []
        examined = 0
        for block_index, block in enumerate(blocks):
            try:
                payload = json.loads(
                    str(block), parse_constant=_reject_constant, object_pairs_hook=_unique_keys
                )
            except (ValueError, RecursionError):
                issues.append(Issue(code="invalid_jsonld"))
                continue
            for item, path in _objects(payload):
                examined += 1
                if examined > limits.max_records:
                    raise ResourceLimitError("JSON-LD object count exceeds max_records")
                item_types = item.get("@type", [])
                if isinstance(item_types, str):
                    item_types = [item_types]
                if recipe.item_type and (
                    not isinstance(item_types, list) or recipe.item_type not in item_types
                ):
                    continue
                values: dict[str, object] = {}
                locators: dict[str, str] = {}
                for field, pointer in recipe.fields.items():
                    value = _resolve(item, pointer)
                    if value is not _MISSING:
                        values[field] = value
                        locators[field] = f"jsonld[{block_index}]#{path}{pointer}"
                candidates.append(Candidate(values, locators))
        return ParsedDocument(tuple(candidates), tuple(issues))


def _reject_constant(value: str) -> object:
    raise ValueError("Non-finite JSON constants are not allowed")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are ambiguous")
        result[key] = value
    return result
