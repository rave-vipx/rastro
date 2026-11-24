"""Discover repeated records and their per-record fields from a parsed page.

Page content is untrusted: selectors come only from safe tags and stable classes, and the
number of candidate record selectors and scanned nodes is bounded, so a hostile page
fails with ``ResourceLimitError`` instead of consuming unbounded CPU.
"""

from __future__ import annotations

from cssselect import SelectorError
from cssselect.xpath import ExpressionError
from scrapling.parser import Selector

from rastro.adapters.page_outline import LxmlPageOutliner
from rastro.adapters.scrapling import _parse_html
from rastro.adapters.suggest_dom import Element, elements, stable_classes, tag_of
from rastro.adapters.suggest_fields import MAX_FIELD_SELECTORS, relative_selectors
from rastro.domain.models import Limits, ResourceLimitError
from rastro.domain.page_outline import ContainerCandidate
from rastro.domain.suggest import normalize_text
from rastro.ports.field_discovery import DiscoveredField, DiscoveredRecords

MAX_RECORD_SELECTORS = 64
MAX_RECORD_NODES = 200
_VALUE_ATTRIBUTES = ("datetime", "href", "src", "content")


class ScraplingFieldDiscoverer:
    def discover(
        self, html: str, records_min: int, limits: Limits
    ) -> tuple[DiscoveredRecords, ...]:
        outline = LxmlPageOutliner().outline(html, limits)
        if not html.strip():
            return ()
        page = _parse_html(html)
        results = []
        for rec_sel in _candidate_selectors(page._root, outline.containers, records_min):
            try:
                records = tuple(page.css(rec_sel))
            except (SelectorError, ExpressionError):
                continue
            if not records_min <= len(records) <= limits.max_records:
                continue
            if fields := _record_fields(records):
                results.append(DiscoveredRecords(rec_sel, fields))
        return tuple(results)


def _signature(node: Element) -> str | None:
    tag = tag_of(node)
    return None if tag is None else tag + "".join(f".{c}" for c in stable_classes(node))


def _candidate_selectors(
    root: Element, containers: tuple[ContainerCandidate, ...], records_min: int
) -> list[str]:
    selectors = {c.selector: None for c in containers}
    for parent in elements(root):
        if (psig := _signature(parent)) is None:
            continue
        counts: dict[str, int] = {}
        for child in parent:
            if isinstance(child.tag, str) and (csig := _signature(child)):
                counts[csig] = counts.get(csig, 0) + 1
        for csig, n in counts.items():
            if n >= records_min:
                selectors.setdefault(f"{psig} > {csig}", None)
        if len(selectors) > MAX_RECORD_SELECTORS:
            raise ResourceLimitError(
                f"HTML has more than {MAX_RECORD_SELECTORS} candidate record selectors"
            )
    return list(selectors)


def _record_fields(records: tuple[Selector, ...]) -> tuple[DiscoveredField, ...]:
    first_root, found, used = records[0]._root, [], set[str]()
    for node in elements(first_root)[1 : MAX_RECORD_NODES + 1]:
        tag, classes = tag_of(node) or "field", tuple(stable_classes(node))
        attrs = [a for a in _VALUE_ATTRIBUTES if node.get(a)]
        specs: list[tuple[str, str | None]] = [(f"::attr({a})", a) for a in attrs]
        if (node.text or "").strip() and "datetime" not in attrs and "href" not in attrs:
            specs.append(("::text", None))
        for suffix, attr in specs:
            for rel in relative_selectors(first_root, node)[:MAX_FIELD_SELECTORS]:
                sel = rel + suffix
                if not rel or sel in used:
                    continue
                vals = [
                    [normalize_text(str(v)) for v in r.css(sel).getall() if normalize_text(str(v))]
                    for r in records
                    if len(r.css(rel)) <= 1
                ]
                if len(vals) == len(records) and all(len(v) == 1 for v in vals):
                    used.add(sel)
                    found.append(
                        DiscoveredField(tag, classes, attr, sel, tuple(v[0] for v in vals))
                    )
                    break
    return tuple(found)
