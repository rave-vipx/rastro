"""Outline a page's repeated structure for model prompts (AI-001, INT-002).

Selectors are built only from safe tag names and stable class tokens (as in suggestion);
texts and attribute values are clipped and stay untrusted. Deterministic and offline.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator

from rastro.adapters.scrapling import _parse_html
from rastro.adapters.suggest_dom import Element, elements, stable_classes, tag_of
from rastro.domain.models import Limits, ResourceLimitError
from rastro.domain.page_outline import (
    ContainerCandidate,
    OutlineNode,
    PageOutline,
    clip_untrusted,
)
from rastro.domain.suggest import normalize_text

MAX_CONTAINERS = 6
MAX_SAMPLES = 2
MAX_SAMPLE_NODES = 20
MAX_SAMPLE_DEPTH = 4
MAX_HEADINGS = 15
MAX_ATTRIBUTE_KINDS = 12
TEXT_CHARS = 60
VALUE_CHARS = 80
_SKIPPED = frozenset({"script", "style", "template", "noscript", "head"})
_VALUE_ATTRIBUTES = ("href", "src", "datetime", "content")
_URL = re.compile(r"(?:https?://|/|\./)\S")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ATTRIBUTE = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9:/._-]{0,63}")


class LxmlPageOutliner:
    """Never fetches, renders or runs page scripts."""

    def outline(self, html: str, limits: Limits) -> PageOutline:
        if len(html.encode()) > limits.max_html_bytes:
            raise ResourceLimitError("HTML input exceeds max_html_bytes")
        root = _parse_html(html)._root
        nodes = elements(root)
        title = root.find(".//title")
        return PageOutline(
            title=_clip(title.text_content(), VALUE_CHARS) if title is not None else "",
            headings=tuple(
                text
                for node in nodes
                if str(node.tag).lower() in {"h1", "h2", "h3"}
                and (text := _clip(node.text_content(), 100))
            )[:MAX_HEADINGS],
            jsonld_types=_jsonld_types(root, limits),
            value_attributes=_value_attributes(nodes),
            containers=_containers(nodes),
        )


def _clip(text: str, limit: int) -> str:
    return clip_untrusted(normalize_text(text), limit)


def _signature(node: Element) -> str | None:
    tag = tag_of(node)
    return None if tag is None else tag + "".join(f".{c}" for c in stable_classes(node))


def _children(node: Element) -> Iterator[Element]:
    return (
        child
        for child in node
        if isinstance(child.tag, str) and tag_of(child) and child.tag.lower() not in _SKIPPED
    )


def _containers(nodes: list[Element]) -> tuple[ContainerCandidate, ...]:
    groups: dict[str, list[Element]] = {}
    for parent in nodes:
        parent_signature = _signature(parent)
        if parent_signature is None:
            continue
        siblings: dict[str, list[Element]] = {}
        for child in _children(parent):
            child_signature = _signature(child)
            if child_signature is not None:
                siblings.setdefault(child_signature, []).append(child)
        for child_signature, members in siblings.items():
            if len(members) >= 2:  # a single child per parent is not a list
                key = f"{parent_signature} > {child_signature}"
                groups.setdefault(key, []).extend(members)
    scored = []
    for selector, members in groups.items():
        richness = min(_richness(members[0]), 10)
        if len(members) >= 3 and richness:
            scored.append((-len(members) * richness, selector, members))
    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        ContainerCandidate(
            selector=selector,
            count=len(members),
            samples=tuple(_node(m, MAX_SAMPLE_DEPTH, [0]) for m in members[:MAX_SAMPLES]),
        )
        for _, selector, members in scored[:MAX_CONTAINERS]
    )


def _richness(node: Element) -> int:
    """Descendants carrying a direct text or a value attribute, bounded."""
    count = 0
    for item in node.iter():
        if isinstance(item.tag, str) and ((item.text or "").strip() or _values(item)):
            count += 1
            if count >= 50:
                break
    return count


def _values(node: Element) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, _clip(str(node.get(name)), VALUE_CHARS))
        for name in _VALUE_ATTRIBUTES
        if node.get(name) is not None
    )


def _node(node: Element, depth: int, budget: list[int]) -> OutlineNode:
    budget[0] += 1
    children: list[OutlineNode] = []
    if depth > 0:
        for child in _children(node):
            if budget[0] >= MAX_SAMPLE_NODES:
                break
            children.append(_node(child, depth - 1, budget))
    return OutlineNode(
        tag=tag_of(node) or "",
        classes=tuple(stable_classes(node)),
        text=_clip(node.text or "", TEXT_CHARS),
        attributes=_values(node),
        children=tuple(children),
    )


def _value_attributes(nodes: list[Element]) -> tuple[tuple[str, int], ...]:
    counts: Counter[str] = Counter()
    for node in nodes:
        tag = tag_of(node)
        for name, raw in node.attrib.items():
            name, value = str(name).lower(), str(raw).strip()
            looks_like_value = name == "datetime" or _URL.match(value) or _DATE.search(value)
            if tag and looks_like_value and _ATTRIBUTE.fullmatch(name):
                counts[f"{tag}[{name}]"] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:MAX_ATTRIBUTE_KINDS])


def _jsonld_types(root: Element, limits: Limits) -> tuple[str, ...]:
    found: list[str] = []
    for index, script in enumerate(root.iter("script")):
        if index >= limits.max_jsonld_blocks:
            break
        if str(script.get("type", "")).strip().lower() != "application/ld+json":
            continue
        try:
            value = json.loads(script.text or "")
        except (ValueError, RecursionError):
            continue
        _collect_types(value, found, 4)
    return tuple(dict.fromkeys(found))[:12]


def _collect_types(value: object, found: list[str], depth: int) -> None:
    if depth < 0 or len(found) >= 32:
        return
    if isinstance(value, list):
        for item in value[:32]:
            _collect_types(item, found, depth - 1)
    elif isinstance(value, dict):
        kinds = value.get("@type")
        for kind in kinds if isinstance(kinds, list) else [kinds]:
            if isinstance(kind, str) and _TYPE.fullmatch(kind):
                found.append(kind)
        for key in ("@graph", "itemListElement", "item"):
            if key in value:
                _collect_types(value[key], found, depth - 1)
