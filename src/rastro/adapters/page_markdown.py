"""A page as short Markdown for a model, after hidden content is removed (ADR 0035).

Implements ``PageCondenser``. The page first goes through ``harden_tree`` (hidden
elements, comments, templates and invisible characters are dropped), then scripts,
styles and embedded media go, and only visible text is written: headings, paragraphs,
list items and table rows. Link targets and image ``alt`` text are left out, so queries
and text nobody sees never reach a prompt. Extraction always runs on the original page.
"""

from __future__ import annotations

import re
from typing import Any

from lxml.etree import LxmlError  # type: ignore[import-untyped]
from scrapling.parser import Selector

from rastro.adapters.html_hardening import harden_tree
from rastro.domain.models import ConfigurationError

__all__ = ["MarkdownCondenser"]

_SKIPPED = frozenset(
    {"head", "script", "style", "svg", "iframe", "img", "picture", "video", "audio", "canvas"}
    | {"object", "embed", "form", "select", "option", "button", "input", "textarea"}
)
_BLOCKS = frozenset(
    {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "ul", "ol"}
    | {"dl", "dt", "dd", "blockquote", "pre", "figure", "figcaption", "table", "tbody"}
    | {"thead", "tfoot", "address", "details", "summary", "body", "html"}
)
_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_SPACE = re.compile(r"\s+")
_MAX_DEPTH = 200  # deeper subtrees are flattened to their text


class MarkdownCondenser:
    """Condense HTML into Markdown a person would read, clipped to ``max_chars``."""

    def condense(self, html: str, *, max_chars: int) -> str:
        if max_chars < 1:
            raise ConfigurationError("max_chars must be at least 1")
        try:
            page = Selector(html, adaptive=False, huge_tree=False, keep_comments=True)
        except (ValueError, LxmlError):
            raise ConfigurationError("Invalid HTML document") from None
        if page._root is None:
            raise ConfigurationError("Invalid HTML document")
        harden_tree(page._root)
        blocks: list[str] = []
        _walk(page._root, blocks, 0)
        text = "\n\n".join(b for b in blocks if b)
        return text[:max_chars].rstrip()


def _walk(node: Any, blocks: list[str], depth: int) -> None:
    """Append one Markdown block per visible heading, row, item or run of inline text."""
    inline: list[str] = [node.text or ""]

    def flush() -> None:
        line = _SPACE.sub(" ", "".join(inline)).strip()
        if line:
            blocks.append(line)
        inline.clear()

    for child in node:
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        if tag in _SKIPPED or not tag:
            inline.append(child.tail or "")
            continue
        if tag == "br":
            inline.append("\n")
        elif tag in _HEADINGS:
            flush()
            blocks.append("#" * _HEADINGS[tag] + " " + _text(child))
        elif tag == "li":
            flush()
            blocks.append("- " + _text(child))
        elif tag == "tr":
            flush()
            cells = [_text(cell) for cell in child if str(cell.tag).lower() in {"td", "th"}]
            blocks.append("| " + " | ".join(cells) + " |")
        elif tag in _BLOCKS and depth < _MAX_DEPTH:
            flush()
            _walk(child, blocks, depth + 1)
        else:
            inline.append(_text(child) if tag not in _BLOCKS else " " + _text(child) + " ")
        inline.append(child.tail or "")
    flush()


def _text(node: Any) -> str:
    """Visible text of ``node`` on one line, without skipped subtrees."""
    parts: list[str] = []
    for item in node.iter():
        tag = item.tag.lower() if isinstance(item.tag, str) else ""
        if tag in _SKIPPED or any(
            isinstance(a.tag, str) and a.tag.lower() in _SKIPPED for a in item.iterancestors()
        ):
            continue
        parts.append(item.text or "")
        if item is not node:
            parts.append(item.tail or "")
    return _SPACE.sub(" ", "".join(parts)).strip()
