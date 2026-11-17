"""Remove what a person cannot see before a page reaches a model (N19, SEC-008, AI-001).

Model contexts only: record extraction always runs on the original document. Dropped are
elements with ``hidden`` or ``aria-hidden="true"``, inline styles that hide them
(``display:none``, ``visibility:hidden``, ``opacity:0``, ``font-size:0``, zero width and
height, far off-screen offsets), comments, ``<template>`` and ``<noscript>``. Text is
NFKC-folded and stripped of control, format (zero-width, bidi, Unicode tag) and
line-separator characters. Attributes are kept: selectors must still match the page.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import lxml.html  # type: ignore[import-untyped]
from lxml import etree
from lxml.etree import LxmlError  # type: ignore[import-untyped]
from scrapling.parser import Selector

from rastro.adapters.suggest_dom import Element
from rastro.domain.models import ConfigurationError
from rastro.domain.page_outline import clip_untrusted

_DROPPED_TAGS = frozenset({"template", "noscript"})
_ZERO = r"\s*:\s*(?:0+(?:\.0*)?|\.0+)(?:px|em|rem|pt|%)?\s*(?:;|!|$)"
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)"
    r"|(?<![-\w])(?:opacity|font-size)"
    + _ZERO
    + r"|(?<![-\w])(?:left|top|right|bottom|text-indent|margin-left|margin-top)\s*:\s*-\d{3,}",
    re.I,
)
_ZERO_WIDTH = re.compile(r"(?<![-\w])(?:max-)?width" + _ZERO, re.I)
_ZERO_HEIGHT = re.compile(r"(?<![-\w])(?:max-)?height" + _ZERO, re.I)


@dataclass(frozen=True)
class HardenedHtml:
    html: str
    removed: dict[str, int] = field(default_factory=dict)


def harden_html(html: str) -> HardenedHtml:
    """The page as a model may see it, and how many nodes of each kind were removed."""
    try:
        page = Selector(html, adaptive=False, huge_tree=False, keep_comments=True)
    except (ValueError, LxmlError):
        raise ConfigurationError("Invalid HTML document") from None
    if page._root is None:
        raise ConfigurationError("Invalid HTML document")
    removed = harden_tree(page._root)
    return HardenedHtml(lxml.html.tostring(page._root, encoding="unicode"), removed)


def harden_tree(root: Element) -> dict[str, int]:
    """Drop invisible nodes and clean text in place; count removals by kind."""
    counts: Counter[str] = Counter()
    doomed: list[tuple[Element, str]] = []
    gone: set[int] = set()
    for node in root.iter():
        kind = _hidden_kind(node)
        if kind is not None:
            doomed.append((node, kind))
    for node, kind in doomed:
        gone.add(id(node))
        if not any(id(parent) in gone for parent in node.iterancestors()):
            counts[kind] += 1
            _drop(node)
    for node in root.iter():
        node.text = _clean(node.text)
        node.tail = _clean(node.tail)
    return dict(sorted(counts.items()))


def _hidden_kind(node: Element) -> str | None:
    if node.tag is etree.Comment:
        return "comment"
    if not isinstance(node.tag, str):
        return "non_element"
    tag = node.tag.lower()
    if tag in _DROPPED_TAGS:
        return tag
    if node.get("hidden") is not None:
        return "hidden_attribute"
    if str(node.get("aria-hidden", "")).strip().lower() == "true":
        return "aria_hidden"
    style = str(node.get("style", ""))
    if style and (
        _HIDDEN_STYLE.search(style) or (_ZERO_WIDTH.search(style) and _ZERO_HEIGHT.search(style))
    ):
        return "hidden_style"
    return None


def _drop(node: Element) -> None:
    """Remove ``node`` and its subtree, keeping the text that follows it.

    A hidden root has no parent to leave it: it is emptied, attributes included.
    """
    parent = node.getparent()
    if parent is None:
        node.clear()
        return
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _clean(text: str | None) -> str | None:
    if not text:
        return text
    folded = unicodedata.normalize("NFKC", text)
    edge = " " if folded[:1].isspace() else ""
    tail = " " if folded[-1:].isspace() else ""
    cleaned = clip_untrusted(folded, len(folded) + 1)
    return f"{edge}{cleaned}{tail}" if cleaned else (" " if edge or tail else "")
