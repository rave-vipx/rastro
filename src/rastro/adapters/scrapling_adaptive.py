"""Adaptive locating with the default engine: similar records, stable selectors, label anchors.

Implements ``AdaptiveLocator`` with Scrapling's ``find_by_text``, ``find_by_regex``,
``find_similar`` and ``generate_css_selector``. It only proposes selectors; every
candidate built from them still meets recipe CI. Pages are parsed within the same
structural budgets as relocation, and nothing here logs page content.
"""

from __future__ import annotations

import re
from typing import Any

from cssselect import SelectorError
from lxml.etree import LxmlError  # type: ignore[import-untyped]
from scrapling.parser import Selector

from rastro.adapters.scrapling_container import identifier
from rastro.domain.models import ConfigurationError, Limits, ResourceLimitError
from rastro.ports.engine import RecordCandidate

__all__ = ["ScraplingAdaptive", "bounded_page", "element_names", "relative_path"]

MAX_ANCHOR_CHARS = 512
MAX_LABEL_RECORDS = 20  # records searched for a label before giving up
_MIN_PARTIAL = 3  # shorter anchors must match a whole element text
_ROOTS = frozenset({"html", "body"})

type Names = dict[Any, tuple[str, tuple[str, ...]]]


class ScraplingAdaptive:
    """Find records and field locators from what a page shows, not from a fixed path."""

    def similar_records(
        self, html: str, anchor_text: str, *, limits: Limits
    ) -> tuple[RecordCandidate, ...]:
        """Every repeated ancestor group of the element showing ``anchor_text``.

        Each group carries one field locator, keyed by ``anchor_text``: the path from
        the record to that element. Groups are listed from the innermost outwards.
        """
        anchor = anchor_text.strip()
        page = bounded_page(html, limits, "adaptive")
        if not anchor or len(anchor) > MAX_ANCHOR_CHARS:
            return ()
        leaf = _find_text(page, anchor)
        if leaf is None:
            return ()
        names = element_names(page)
        found: list[RecordCandidate] = []
        node = leaf.parent
        while node is not None and node.tag not in _ROOTS:
            group = _sibling_group(node)
            selector = _group_selector(page, node, group) if len(group) > 1 else None
            path = relative_path(node._root, leaf._root, names) if selector else None
            if selector is not None and path is not None:
                found.append(RecordCandidate(selector, len(group), {anchor_text: path}))
            node = node.parent
        return tuple(found)

    def stable_selector(self, html: str, locator: str, *, limits: Limits) -> str | None:
        """A class- or id-based selector for the first match; positional only as a last resort."""
        page = bounded_page(html, limits, "adaptive")
        nodes = _css(page, locator)
        return _stable(page, nodes[0]) if nodes else None

    def anchor_by_label(
        self, html: str, record_selector: str, label: str, *, limits: Limits
    ) -> str | None:
        """The path, from each record, to the value shown next to ``label``."""
        words = label.split()
        page = bounded_page(html, limits, "adaptive")
        if not words or len(label) > MAX_ANCHOR_CHARS:
            return None
        pattern = r"^\s*" + r"\s+".join(re.escape(w) for w in words) + r"\s*:?\s*$"
        names = element_names(page)
        for record in _css(page, record_selector)[:MAX_LABEL_RECORDS]:
            hit = record.find_by_regex(pattern, first_match=True)
            if not hit:
                continue
            value = _labelled_value(hit._root, record._root)
            path = relative_path(record._root, value, names) if value is not None else None
            if path is not None:
                return path
        return None


def bounded_page(html: str, limits: Limits, what: str) -> Selector:
    """Parse ``html`` within the byte, node, depth and attribute budgets; drop comments."""
    try:
        size = len(html.encode())
    except UnicodeError:
        raise ConfigurationError(f"{what.capitalize()} input must be valid UTF-8") from None
    if size > limits.max_html_bytes:
        raise ResourceLimitError("HTML input exceeds max_html_bytes")
    try:
        page = Selector(html or "<html></html>", adaptive=False, huge_tree=False)
    except (ValueError, LxmlError):
        raise ConfigurationError(f"Invalid {what} HTML") from None
    if page._root is None:
        return page
    for comment in page._root.xpath("//comment() | //processing-instruction()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    nodes = list(page._root.iter())
    if len(nodes) > 4096:
        raise ResourceLimitError(f"{what.capitalize()} exceeds 4096 nodes")
    for node in nodes:
        if len(list(node.iterancestors())) >= 128 or len(node.attrib) > 100:
            raise ResourceLimitError(f"{what.capitalize()} exceeds structural budgets")
    return page


def element_names(page: Selector) -> Names:
    """Tag and sorted class tokens of every element, keyed by the lxml element."""
    if page._root is None:
        return {}
    return {
        node: (node.tag, tuple(sorted(set(node.get("class", "").split()))))
        for node in page._root.iter()
    }


def relative_path(record: Any, node: Any, names: Names) -> str | None:
    """A tag+class child path from ``record`` to ``node``; None when a step is not unique.

    Positional steps would read a sibling in a record that omits an optional child.
    """
    parts: list[str] = []
    current = node
    while current is not record:
        parent = current.getparent()
        if parent is None:
            raise ConfigurationError("Locator escaped the record")
        siblings = [child for child in parent if isinstance(child.tag, str)]
        if sum(names[child] == names[current] for child in siblings) != 1:
            return None
        tag, classes = names[current]
        parts.append(identifier(tag) + "".join("." + identifier(c) for c in classes))
        current = parent
    return " > ".join([":scope", *reversed(parts)])


def _find_text(page: Selector, anchor: str) -> Selector | None:
    exact = page.find_by_text(anchor, first_match=True)
    if exact:
        return exact
    if len(anchor) < _MIN_PARTIAL:
        return None
    partial = page.find_by_text(anchor, first_match=True, partial=True)
    return partial or None


def _sibling_group(node: Selector) -> list[Selector]:
    parent = node._root.getparent()
    similar = [s for s in node.find_similar() if s._root.getparent() is parent]
    return [node, *similar]


def _shape(node: Selector, common: set[str]) -> str:
    return identifier(str(node.tag)) + "".join("." + identifier(c) for c in sorted(common))


def _group_selector(page: Selector, node: Selector, group: list[Selector]) -> str | None:
    """Tag and shared classes; scoped under the parent when that alone is not exact."""
    common = set.intersection(*(set(str(n.attrib.get("class", "")).split()) for n in group))
    selector = _shape(node, common)
    if len(_css(page, selector)) == len(group):
        return selector
    parent = node.parent
    if parent is None:
        return None
    scoped = f"{_stable(page, parent)} > {selector}"
    return scoped if len(_css(page, scoped)) == len(group) else None


def _stable(page: Selector, node: Selector) -> str:
    ident = str(node.attrib.get("id", "")).strip()
    if ident and len(_css(page, "#" + identifier(ident))) == 1:
        return "#" + identifier(ident)
    shaped = _shape(node, set(str(node.attrib.get("class", "")).split()))
    if len(_css(page, shaped)) == 1:
        return shaped
    return str(node.generate_css_selector)


def _labelled_value(label: Any, record: Any) -> Any | None:
    """The element child of the label, else its next element, else its parent's next one."""
    for child in label:
        if isinstance(child.tag, str) and "".join(child.itertext()).strip():
            return child
    current = label
    while current is not None and current is not record:
        following = current.getnext()
        while following is not None and not isinstance(following.tag, str):
            following = following.getnext()
        if following is not None and "".join(following.itertext()).strip():
            return following
        current = current.getparent()
    return None


def _css(page: Selector, expression: str) -> list[Selector]:
    try:
        return list(page.css(expression))
    except SelectorError:
        raise ConfigurationError("Invalid CSS selector") from None
