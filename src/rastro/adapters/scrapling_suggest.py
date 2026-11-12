"""Example-driven CSS recipe candidates through Scrapling (INT-002, DISC-003).

Every example value is located in the page, the matches of one example record are
generalized to a repeating record container, and each field gets the shortest selector,
relative to the record, that yields at most one element per record and reproduces the
examples. The application verifies candidates through the normal extraction pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from cssselect import SelectorError
from cssselect.xpath import ExpressionError
from pydantic import ValidationError
from scrapling.parser import Selector

from rastro.adapters.scrapling import _parse_html
from rastro.adapters.suggest_dom import (
    Element,
    Match,
    elements,
    holds_all,
    locate,
    signatures,
)
from rastro.adapters.suggest_fields import RecordSet, example_field, microdata_field
from rastro.domain.models import CssRecipe, Limits, ResourceLimitError
from rastro.domain.suggest import Example, FieldConfidence, SuggestionError
from rastro.ports.suggest import RecipeCandidate

MAX_DEPTH = 32
MAX_RECORD_SELECTORS = 64
_ROOTS = frozenset({"html", "body"})

type Located = list[dict[str, list[Match]]]


class ScraplingSuggester:
    """Deterministic, offline; never fetches, renders or runs page scripts."""

    def suggest(
        self,
        html: str,
        fields: tuple[str, ...],
        examples: Sequence[Example],
        limits: Limits,
        *,
        records_min: int,
        max_candidates: int,
        base_url: str | None = None,
    ) -> tuple[RecipeCandidate, ...]:
        if len(html.encode()) > limits.max_html_bytes:
            raise ResourceLimitError("HTML input exceeds max_html_bytes")
        if not html.strip():
            raise SuggestionError("The page is empty")
        page = _parse_html(html)
        nodes = elements(page._root)
        located = _locate_all(nodes, examples, base_url)
        candidates: list[RecipeCandidate] = []
        for selector in _record_selectors(page, located, records_min, limits):
            records = RecordSet.of(page, selector, located)
            if records is None:
                continue
            candidate = _build(records, fields, examples, base_url)
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) >= max_candidates:
                break
        if not candidates:
            raise SuggestionError(
                f"No container that repeats at least {records_min} times holds every example "
                "field; check the examples or lower records_min for a single record"
            )
        return tuple(candidates)


def _locate_all(nodes: list[Element], examples: Sequence[Example], base_url: str | None) -> Located:
    located: Located = []
    missing: list[str] = []
    for position, example in enumerate(examples, 1):
        found = {name: locate(nodes, value, base_url) for name, value in example.items()}
        missing.extend(f"example {position} {name!r}" for name, hits in found.items() if not hits)
        located.append(found)
    if missing:
        raise SuggestionError("Example values not found in the page: " + ", ".join(missing))
    return located


def _record_selectors(
    page: Selector, located: Located, records_min: int, limits: Limits
) -> Iterator[str]:
    seen: set[str] = set()
    for found in located:
        anchor = min(found, key=lambda name: (len(found[name]), name))
        for match in found[anchor]:
            for selector in _climb(match.element, found):
                if selector in seen:
                    continue
                seen.add(selector)
                if len(seen) > MAX_RECORD_SELECTORS:
                    return
                if _repeats(page, selector, records_min, limits):
                    yield selector


def _climb(start: Element, found: dict[str, list[Match]]) -> Iterator[str]:
    """Selectors of ``start`` and its ancestors that hold a match of every example field."""
    node = start
    for _ in range(MAX_DEPTH):
        if node is None or str(node.tag).lower() in _ROOTS:
            return
        if holds_all(node, found):
            own = signatures(node)
            yield from own
            parent = node.getparent()
            if parent is not None:
                for context in signatures(parent):
                    yield from (f"{context} > {item}" for item in own)
        node = node.getparent()


def _repeats(page: Selector, selector: str, records_min: int, limits: Limits) -> bool:
    try:
        nodes = [item._root for item in page.css(selector)]
    except (SelectorError, ExpressionError):
        return False
    if not records_min <= len(nodes) <= limits.max_records:
        return False
    # Nested matches would turn one card and its inner block into two records.
    chosen = set(map(id, nodes))
    return not any(id(parent) in chosen for node in nodes for parent in node.iterancestors())


def _build(
    records: RecordSet,
    fields: tuple[str, ...],
    examples: Sequence[Example],
    base_url: str | None,
) -> RecipeCandidate | None:
    mapping: dict[str, str] = {}
    confidence: dict[str, FieldConfidence] = {}
    for name in fields:
        if any(name in example for example in examples):
            note = example_field(records, name, examples, base_url)
            if note is None:
                return None
        else:
            note = microdata_field(records, name)
        confidence[name] = note
        if note.selector is not None:
            mapping[name] = note.selector
    if not mapping:
        return None
    try:
        recipe = CssRecipe(record_selector=records.selector, fields=mapping)
    except ValidationError:
        return None
    return RecipeCandidate(recipe, confidence)
