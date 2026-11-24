"""Elements inside the first record that a person can click to fix a field.

Each candidate carries the element path ``pick`` accepts, a short text and the positional
locator the browser measures its box with. The page is parsed within the engine's
structural budgets; text is untrusted page data and is clipped.
"""

from __future__ import annotations

from typing import Any

from rastro.domain.models import Limits
from rastro.studio.pick_element import _engine, match_count

MAX_CANDIDATES = 20
MAX_TEXT = 80


def _path(root: Any, node: Any) -> list[int] | None:
    steps: list[int] = []
    while node is not root:
        parent = node.getparent()
        if parent is None:
            return None
        steps.append([c for c in parent if isinstance(c.tag, str)].index(node))
        node = parent
    return steps[::-1]


def _own_text(node: Any) -> str:
    return " ".join("".join(node.itertext()).split())[:MAX_TEXT]


def candidates(html: str, record_selector: str, limits: Limits) -> list[dict[str, Any]]:
    """Leaf-like elements of the first record with visible text, at most ``MAX_CANDIDATES``."""
    if not record_selector or match_count(html, record_selector) == 0:
        return []
    try:
        page = _engine().bounded_page(html, limits, "pick")
        first = page.css(record_selector)[0]._root
    except Exception:
        return []
    found: list[dict[str, Any]] = []
    for node in first.iter():
        if not isinstance(node.tag, str) or not (node.text or "").strip():
            continue
        path = _path(page._root, node)
        text = _own_text(node)
        if path is None or not text:
            continue
        locator = " > ".join(["html", *(f":nth-child({i + 1})" for i in path)])
        found.append({"path": path, "text": text, "locator": locator})
        if len(found) == MAX_CANDIDATES:
            break
    return found
