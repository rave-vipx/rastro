"""Offline parsing through Scrapling. No fetchers, rendering, or adaptive storage."""

from __future__ import annotations

from collections.abc import Iterator

from cssselect import SelectorError
from scrapling.parser import Selector

from rastro.domain.json import MISSING, resolve_pointer, strict_json
from rastro.domain.models import ConfigurationError, Issue, Limits, Recipe, ResourceLimitError
from rastro.ports.parser import Candidate, ParsedDocument


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
                payload = strict_json(str(block))
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
                    value = resolve_pointer(item, pointer)
                    if value is not MISSING:
                        values[field] = value
                        locators[field] = f"jsonld[{block_index}]#{path}{pointer}"
                candidates.append(Candidate(values, locators))
        return ParsedDocument(tuple(candidates), tuple(issues))
