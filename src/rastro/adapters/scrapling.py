"""Offline parsing through Scrapling. No fetchers, rendering, or adaptive storage."""

from __future__ import annotations

from collections.abc import Iterator

from cssselect import SelectorError, parse
from lxml.etree import LxmlError  # type: ignore[import-untyped]
from scrapling.parser import Selector

from rastro.domain.json import MISSING, resolve_pointer, strict_json
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Issue,
    JsonLdRecipe,
    Limits,
    Recipe,
    ResourceLimitError,
)
from rastro.domain.recipes import DOCUMENT_PREFIX, EXPAND_PREFIX
from rastro.ports.parser import Candidate, ParsedDocument


def _parse_html(html: str) -> Selector:
    try:
        page = Selector(html, adaptive=False, huge_tree=False)
    except (ValueError, LxmlError):
        raise ConfigurationError("Invalid HTML document") from None
    if page._root is None:
        raise ConfigurationError("Invalid HTML document")
    return page


def field_scope(expression: str, page: Selector, node: Selector) -> tuple[Selector, str]:
    """Return the element a CSS field expression is evaluated against, and its selector."""
    expression = expression.removeprefix(EXPAND_PREFIX)
    if expression.startswith(DOCUMENT_PREFIX):
        return page, expression.removeprefix(DOCUMENT_PREFIX)
    return node, expression


def element_count(scope: Selector, selector: str) -> int:
    """Elements a selector list matches; pseudo-elements select values of their element."""
    count = 0
    seen: set[str] = set()
    for tree in parse(selector):
        if tree.canonical().startswith("::"):
            count += 1
            continue
        tree.pseudo_element = None
        # A selector list ("a, b") matching the same element twice is one match.
        seen.update(
            element._root.getroottree().getpath(element._root)
            for element in scope.css(tree.canonical())
        )
    return count + len(seen)


def _matches(scope: Selector, selector: str) -> tuple[int, list[str]]:
    """Element count and values. Blank text around nested markup is not evidence only
    when one element matched; an element holding only blank text yields "", and empty
    attributes stay ""."""
    elements = element_count(scope, selector)
    values = [str(value) for value in scope.css(selector).getall()]
    if elements == 1 and "::text" in selector:
        values = [value for value in values if value.strip()] or values[:1]
    return elements, values


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


def _node_candidates(
    page: Selector, node: Selector, index: int, recipe: CssRecipe
) -> list[Candidate]:
    """One candidate per record node, or one per value of its single ``expand:`` field."""
    values: dict[str, object] = {}
    locators: dict[str, str] = {}
    issues: list[Issue] = []
    expanded: tuple[str, str, list[str]] | None = None
    base = f"{recipe.record_selector} [match {index}] >> "
    for field, selector in recipe.fields.items():
        scope, css = field_scope(selector, page, node)
        if selector.startswith(EXPAND_PREFIX):
            found = [str(v) for v in scope.css(css).getall()]
            if "::text" in css:
                found = [v for v in found if v.strip()]
            expanded = (field, selector, found)
            continue
        elements, matches = _matches(scope, css)
        if elements > 1 or len(matches) > 1:
            issues.append(Issue(code="ambiguous_match", record_index=index, field=field))
        elif matches:
            values[field] = str(matches[0]).strip()
            locators[field] = f"{base}{selector}"
    if expanded is None or not expanded[2]:
        return [Candidate(values, locators, tuple(issues))]
    field, selector, found = expanded
    return [
        Candidate(
            {**values, field: value.strip()},
            {**locators, field: f"{base}{selector} [value {i}]"},
            tuple(issues),
        )
        for i, value in enumerate(found)
    ]


class ScraplingParser:
    """Translate parser output into bounded, declarative candidates."""

    def parse(self, html: str, recipe: Recipe, limits: Limits) -> ParsedDocument:
        if not isinstance(recipe, CssRecipe | JsonLdRecipe):
            raise ConfigurationError("HTML documents require a css or jsonld recipe")
        if not html.strip():
            return ParsedDocument(())
        page = _parse_html(html)
        try:
            if isinstance(recipe, CssRecipe):
                return self._css(page, recipe, limits)
            return self._jsonld(page, recipe, limits)
        except SelectorError:
            raise ConfigurationError("Recipe contains an invalid CSS selector") from None

    def _css(self, page: Selector, recipe: CssRecipe, limits: Limits) -> ParsedDocument:
        nodes = page.css(recipe.record_selector)
        if len(nodes) > limits.max_records:
            raise ResourceLimitError("Record count exceeds max_records")
        candidates: list[Candidate] = []
        for index, node in enumerate(nodes):
            candidates.extend(_node_candidates(page, node, index, recipe))
            if len(candidates) > limits.max_records:
                raise ResourceLimitError("Record count exceeds max_records")
        return ParsedDocument(tuple(candidates))

    def _jsonld(self, page: Selector, recipe: JsonLdRecipe, limits: Limits) -> ParsedDocument:
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
                for field, mapping in recipe.fields.items():
                    for pointer in (mapping,) if isinstance(mapping, str) else mapping:
                        value = resolve_pointer(item, pointer)
                        if value is not MISSING:
                            values[field] = value
                            locators[field] = f"jsonld[{block_index}]#{path}{pointer}"
                            break
                candidates.append(Candidate(values, locators))
        return ParsedDocument(tuple(candidates), tuple(issues))
