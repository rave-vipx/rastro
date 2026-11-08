"""Offline structural measurements through Scrapling; never imports a fetcher."""

import hashlib
import json

from cssselect import SelectorError, parse
from scrapling.parser import Selector

from rastro.adapters.scrapling import ScraplingParser, _parse_html
from rastro.adapters.structured import JsonParser
from rastro.domain.drift import FieldStructure
from rastro.domain.models import ConfigurationError, CssRecipe, Limits, Recipe, ResourceLimitError


class ScraplingDriftProbe:
    def measure(self, document: str, recipe: Recipe, limits: Limits) -> dict[str, FieldStructure]:
        if len(recipe.fields) > 256:
            raise ResourceLimitError("Drift field count exceeds configured limits")
        if len(document.encode()) > limits.max_input_bytes(recipe.representation):
            raise ResourceLimitError("Drift input exceeds configured limits")
        if not isinstance(recipe, CssRecipe):
            parser = JsonParser() if recipe.representation == "json" else ScraplingParser()
            parsed = parser.parse(document, recipe, limits)
            return {
                name: FieldStructure(
                    selector_match_count=(
                        None
                        if parsed.issues
                        else sum(name in c.locators for c in parsed.candidates)
                    ),
                    field_coverage=0,
                    validation_failure_rate=0,
                )
                for name in sorted(recipe.fields)
            }
        try:
            return self._css(document, recipe, limits)
        except SelectorError:
            raise ConfigurationError("Recipe contains an invalid CSS selector") from None

    def _css(self, document: str, recipe: CssRecipe, limits: Limits) -> dict[str, FieldStructure]:
        page = _parse_html(document or "<html></html>")
        nodes = page.css(recipe.record_selector)
        if len(nodes) > limits.max_records:
            raise ResourceLimitError("Drift record count exceeds configured limits")
        fields = {}
        for name, selector in sorted(recipe.fields.items()):
            count, shapes = 0, []
            # Pseudo-elements select values; fingerprint their owning element's structure.
            selectors = []
            for tree in parse(selector):
                own_value = tree.canonical().startswith("::")
                tree.pseudo_element = None
                selectors.append(None if own_value else tree.canonical())
            for node in nodes:
                count += len(node.css(selector))
                for element_selector in selectors:
                    for element in node.css(element_selector) if element_selector else [node]:
                        shapes.append(_shape(element, limits))
                        if len(shapes) > limits.max_json_nodes:
                            raise ResourceLimitError("Drift matches exceed configured limits")
            digest = hashlib.sha256(json.dumps(sorted(shapes)).encode()).hexdigest()
            fields[name] = FieldStructure(
                selector_match_count=count,
                subtree_fingerprint=digest,
                field_coverage=0,
                validation_failure_rate=0,
            )
        return fields


def _shape(element: Selector, limits: Limits) -> str:
    """Hash tag-only ancestor paths and subtree topology, excluding text and attributes."""
    root = element._root
    ancestors = [str(node.tag) for node in root.iterancestors() if isinstance(node.tag, str)]
    pending = [(root, 0)]
    tags: list[tuple[int, str]] = []
    while pending:
        node, depth = pending.pop()
        if len(tags) >= limits.max_json_nodes or depth > limits.max_json_depth:
            raise ResourceLimitError("Drift subtree exceeds configured limits")
        if isinstance(node.tag, str):
            tags.append((depth, node.tag))
        pending.extend((child, depth + 1) for child in reversed(node))
    return hashlib.sha256(json.dumps([ancestors, tags]).encode()).hexdigest()
