"""Per-field selectors relative to a record container (INT-002, DISC-003)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from cssselect import SelectorError
from cssselect.xpath import ExpressionError
from scrapling.parser import Selector

from rastro.adapters.suggest_dom import (
    Element,
    Match,
    contains,
    holds_all,
    nth_of_type,
    signatures,
)
from rastro.domain.suggest import Example, FieldConfidence, value_matches

MAX_FIELD_SELECTORS = 24
MAX_SCORED_RECORDS = 200
_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}")
_MICRODATA_ATTRIBUTES = ("content", "href", "src", "datetime")


@dataclass(frozen=True)
class RecordSet:
    """Records matched by ``selector`` and, per example, the record holding all its values."""

    selector: str
    records: tuple[Selector, ...]
    owners: tuple[int, ...]
    located: tuple[dict[str, list[Match]], ...]

    @classmethod
    def of(
        cls, page: Selector, selector: str, located: Sequence[dict[str, list[Match]]]
    ) -> RecordSet | None:
        records = tuple(page.css(selector))
        roots = [record._root for record in records]
        owners: list[int] = []
        for found in located:
            owner = next((i for i, root in enumerate(roots) if holds_all(root, found)), None)
            if owner is None:
                return None
            owners.append(owner)
        return cls(selector, records, tuple(owners), tuple(located))

    @property
    def sample(self) -> tuple[Selector, ...]:
        return self.records[:MAX_SCORED_RECORDS]


def relative_selectors(record: Element, node: Element) -> list[str]:
    """Candidate selectors for ``node`` evaluated against ``record`` ("" is the record)."""
    if node is record:
        return [""]
    chain: list[Element] = []
    current = node
    while current is not record:
        chain.append(current)
        current = current.getparent()
    chain.reverse()
    own = signatures(node)
    found = list(own)
    if len(chain) > 1:
        for context in signatures(chain[-2], classed_only=True):
            found.extend(f"{context} > {item}" for item in own)
    path = [nth_of_type(item) for item in chain]
    if all(path):
        found.extend([str(path[-1]), " > ".join(str(item) for item in path)])
    return found


def _count(record: Selector, base: str) -> int | None:
    if not base:
        return 1
    try:
        return len(record.css(base))
    except (SelectorError, ExpressionError):
        return None


def _score(records: RecordSet, base: str) -> int | None:
    """Records with exactly one element, or None when any record has several."""
    counts = [_count(record, base) for record in records.sample]
    if any(count is None or count > 1 for count in counts):
        return None
    return sum(count == 1 for count in counts)


def _reproduces(record: Selector, selector: str, value: str, base_url: str | None) -> bool:
    try:
        values = [str(item) for item in record.css(selector).getall()]
    except (SelectorError, ExpressionError):
        return False
    if selector.endswith("::text"):
        values = [item for item in values if item.strip()]
    return len(values) == 1 and value_matches(value, values[0].strip(), base_url)


def example_field(
    records: RecordSet, name: str, examples: Sequence[Example], base_url: str | None
) -> FieldConfidence | None:
    """The shortest selector reproducing every example of ``name``; None if there is none."""
    pairs: list[tuple[Selector, list[Match], str]] = []
    for owner, found, example in zip(records.owners, records.located, examples, strict=True):
        if name in example:
            record = records.records[owner]
            hits = [hit for hit in found[name] if contains(record._root, hit.element)]
            pairs.append((record, hits, example[name]))
    attribute = pairs[0][1][0].attribute
    suffix = f"::attr({attribute})" if attribute else "::text"
    bases: list[str] = []
    for record, hits, _ in pairs:
        for hit in hits:
            if hit.attribute != attribute:
                continue
            for base in relative_selectors(record._root, hit.element):
                if base not in bases:
                    bases.append(base)
    best: tuple[tuple[int, int], str] | None = None
    for base in bases[:MAX_FIELD_SELECTORS]:
        score = _score(records, base)
        selector = base + suffix
        if score is None or not all(
            _reproduces(record, selector, value, base_url) for record, _, value in pairs
        ):
            continue
        key = (-score, len(selector))
        if best is None or key < best[0]:
            best = (key, selector)
    if best is None:
        return None
    return FieldConfidence(
        field=name,
        method="example",
        selector=best[1],
        examples_total=len(pairs),
        ambiguous=any(len(hits) > 1 for _, hits, _ in pairs),
    )


def microdata_field(records: RecordSet, name: str) -> FieldConfidence:
    """Map a field without examples to an exact ``itemprop`` inside each record, if unique."""
    unmapped = FieldConfidence(
        field=name, method="unmapped", note="No example and no exact microdata itemprop"
    )
    if not _SAFE_NAME.fullmatch(name):
        return unmapped
    base = f'[itemprop~="{name}"]'
    score = _score(records, base)
    if not score:
        return unmapped
    first = next(record.css(base)[0] for record in records.sample if record.css(base))
    attribute = next((a for a in _MICRODATA_ATTRIBUTES if a in first.attrib), None)
    suffix = f"::attr({attribute})" if attribute else "::text"
    return FieldConfidence(
        field=name, method="microdata", selector=base + suffix, note="Exact microdata itemprop"
    )
