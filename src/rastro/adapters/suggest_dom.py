"""Locate example values in a parsed page and describe elements as safe CSS (INT-002).

Page content is untrusted: selectors are built only from tag names and class tokens that
match strict identifier patterns, never from text, ids or generated-looking classes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from rastro.domain.models import ResourceLimitError
from rastro.domain.suggest import normalize_text

MAX_NODES = 50_000
MAX_MATCHES = 64
SAFE_TAG = re.compile(r"[a-z][a-z0-9-]{0,31}")
SAFE_ATTRIBUTE = re.compile(r"[a-z][a-z0-9_.:-]{0,63}")
# No digits and bounded length: hashes and counters make selectors break on the next deploy.
_STABLE_CLASS = re.compile(r"-?[A-Za-z_][A-Za-z_-]{0,39}")
_GENERATED_PREFIXES = ("css-", "sc-", "jsx-", "emotion-", "svelte-", "ng-")
_SKIPPED = frozenset({"script", "style", "template", "noscript", "head"})
_VALUE_ATTRIBUTES = ("href", "src", "content", "datetime")
_URL_ATTRIBUTES = frozenset({"href", "src"})

type Element = Any  # lxml.html.HtmlElement; lxml ships no type stubs


@dataclass(frozen=True)
class Match:
    """An element holding an example value in a direct text node or in ``attribute``."""

    element: Element
    attribute: str | None = None


def elements(root: Element) -> list[Element]:
    """Document-order elements outside script, style and template content, bounded."""
    found: list[Element] = []
    pending = [root]
    while pending:
        node = pending.pop()
        if not isinstance(node.tag, str) or node.tag.lower() in _SKIPPED:
            continue
        found.append(node)
        if len(found) > MAX_NODES:
            raise ResourceLimitError("HTML node count exceeds the suggestion budget")
        pending.extend(reversed(node))
    return found


def _texts(node: Element) -> Iterator[str]:
    if node.text:
        yield normalize_text(node.text)
    for child in node:
        if child.tail:
            yield normalize_text(child.tail)


def _attributes(node: Element) -> Iterator[tuple[str, str]]:
    for name, value in node.attrib.items():
        name = str(name).lower()
        if SAFE_ATTRIBUTE.fullmatch(name) and (
            name in _VALUE_ATTRIBUTES or name.startswith("data-")
        ):
            yield name, str(value)


def locate(nodes: list[Element], value: str, base_url: str | None) -> list[Match]:
    """Matches of the best tier: exact text, exact attribute, contained text, contained
    attribute. Relative ``href``/``src`` values also match their absolute form."""
    tiers: list[list[Match]] = [[], [], [], []]
    for node in nodes:
        texts = list(_texts(node))
        if value in texts:
            tiers[0].append(Match(node))
        elif any(value in text for text in texts):
            tiers[2].append(Match(node))
        for name, raw in _attributes(node):
            text = normalize_text(raw)
            absolute = base_url is not None and name in _URL_ATTRIBUTES
            if text == value or (absolute and urljoin(base_url or "", text) == value):
                tiers[1].append(Match(node, name))
            elif value in text:
                tiers[3].append(Match(node, name))
    return next((tier[:MAX_MATCHES] for tier in tiers if tier), [])


def stable_classes(node: Element) -> list[str]:
    tokens = str(node.get("class", "")).split()
    return sorted(
        {
            token
            for token in tokens
            if _STABLE_CLASS.fullmatch(token) and not token.startswith(_GENERATED_PREFIXES)
        }
    )


def tag_of(node: Element) -> str | None:
    tag = str(node.tag).lower()
    return tag if SAFE_TAG.fullmatch(tag) else None


def signatures(node: Element, *, classed_only: bool = False) -> list[str]:
    """Selectors naming ``node`` alone: ``tag.class`` per stable class, ``tag``, all classes."""
    tag = tag_of(node)
    if tag is None:
        return []
    classes = stable_classes(node)
    found = [f"{tag}.{token}" for token in classes]
    if not classed_only:
        found.append(tag)
    if len(classes) > 1:
        found.append(tag + "".join(f".{token}" for token in classes))
    return found


def nth_of_type(node: Element) -> str | None:
    tag = tag_of(node)
    parent = node.getparent()
    if tag is None or parent is None:
        return None
    position = [child for child in parent if child.tag == node.tag].index(node) + 1
    return f"{tag}:nth-of-type({position})"


def contains(ancestor: Element, node: Element) -> bool:
    return node is ancestor or any(item is ancestor for item in node.iterancestors())


def holds_all(node: Element, found: dict[str, list[Match]]) -> bool:
    """True when ``node`` contains at least one match of every example field."""
    return all(any(contains(node, hit.element) for hit in hits) for hits in found.values())
