"""Resolve a clicked element path to a stable selector on a fetched page.

A path is the child-element index of each step from the ``html`` element. The page is parsed
within the engine's structural budgets; the positional selector is only a locator for the
adaptive engine, which returns an id- or class-based selector when one is unique.
"""

from __future__ import annotations

from typing import Any

from rastro.domain.models import ConfigurationError, Limits

MAX_SAMPLE = 10
MAX_TEXT = 200


def _engine() -> Any:
    try:
        from rastro.adapters import scrapling_adaptive
    except ModuleNotFoundError as err:
        if err.name and err.name.split(".")[0] in {"scrapling", "cssselect", "lxml"}:
            raise ConfigurationError("Install rastro-engine[scrapling] to pick elements") from None
        raise
    return scrapling_adaptive


def _texts(nodes: Any) -> list[str]:
    return [" ".join("".join(n._root.itertext()).split())[:MAX_TEXT] for n in nodes[:MAX_SAMPLE]]


def match_count(html: str, selector: str) -> int:
    """How many elements ``selector`` matches; zero for a selector the page cannot use."""
    try:
        page = _engine().bounded_page(html, Limits(), "pick")
        return len(page.css(selector))
    except Exception:
        return 0


def _shape(page: Any, node: Any, engine: Any) -> str | None:
    """``tag.class`` when the element has classes: it also names its repeated siblings."""
    classes = sorted(set(str(node.get("class", "")).split()))
    if not classes:
        return None
    ident = engine.identifier
    shape = str(node.tag) + "".join("." + ident(c) for c in classes)
    try:
        return shape if any(m._root is node for m in page.css(shape)) else None
    except Exception:
        return None


def pick_element(html: str, path: list[int], limits: Limits) -> dict[str, Any]:
    """Selector, match count and sample texts for the element at ``path``."""
    engine = _engine()
    page = engine.bounded_page(html, limits, "pick")
    node = page._root
    steps: list[str] = []
    for index in path:
        children = [c for c in node if isinstance(c.tag, str)] if node is not None else []
        if index >= len(children):
            raise ConfigurationError("That element is not on the page")
        node = children[index]
        steps.append(f":nth-child({index + 1})")
    locator = " > ".join(["html", *steps])
    chosen = engine.ScraplingAdaptive().stable_selector(html, locator, limits=limits) or locator
    if ":nth" in chosen:
        chosen = _shape(page, node, engine) or chosen
    matched = page.css(chosen)
    return {
        "selector": chosen,
        "tag": str(node.tag),
        "matches": len(matched),
        "sample": _texts(matched),
    }
