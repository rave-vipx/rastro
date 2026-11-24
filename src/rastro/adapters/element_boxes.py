"""Where matched elements sit on a rendered page, in the screenshot's viewport coordinates.

Selectors are recipe CSS: a trailing ``::text`` or ``::attr(name)`` is dropped before the page
is queried, a selector the browser cannot parse measures nothing, and every list is capped.
Only numbers come back from the page; the caller still bounds and validates them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

__all__ = ["MAX_BOXES_PER_SELECTOR", "browser_selector", "measure_boxes"]

MAX_BOXES_PER_SELECTOR = 50
_PSEUDO = re.compile(r"::(?:text|attr\([^()]*\))\s*$")
_MEASURE = """([selectors, limit]) => {
  const out = [];
  for (const selector of selectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(selector)).slice(0, limit); }
    catch (error) { nodes = []; }
    out.push(nodes.map((node) => {
      const r = node.getBoundingClientRect();
      return {x: r.left, y: r.top, w: r.width, h: r.height};
    }));
  }
  return out;
}"""


def browser_selector(selector: str) -> str:
    """The recipe selector without the extraction pseudo-element the browser does not know."""
    return _PSEUDO.sub("", selector).strip()


async def measure_boxes(
    page: Any, selectors: Sequence[str], *, limit: int = MAX_BOXES_PER_SELECTOR
) -> dict[str, list[dict[str, float]]]:
    """Boxes per recipe selector; a selector that matches nothing maps to an empty list."""
    wanted = list(dict.fromkeys(selectors))
    queries = [browser_selector(selector) or ":not(*)" for selector in wanted]
    try:
        measured = await page.evaluate(_MEASURE, [queries, limit])
    except Exception:
        return {selector: [] for selector in wanted}
    if not isinstance(measured, list) or len(measured) != len(wanted):
        return {selector: [] for selector in wanted}
    boxes: dict[str, list[dict[str, float]]] = {}
    for selector, found in zip(wanted, measured, strict=True):
        rows = found[:limit] if isinstance(found, list) else []
        boxes[selector] = [row for row in rows if isinstance(row, dict)]
    return boxes
