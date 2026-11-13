"""A structural outline of a page for model prompts; every text value is untrusted."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

_INVISIBLE = frozenset({"Cc", "Cf", "Zl", "Zp"})


def clip_untrusted(text: str, limit: int) -> str:
    """Drop control, format (bidi, zero-width) and line-separator characters; clip."""
    text = "".join(" " if unicodedata.category(c) in _INVISIBLE else c for c in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class OutlineNode:
    """One element: safe tag and class tokens, clipped direct text and value attributes."""

    tag: str
    classes: tuple[str, ...] = ()
    text: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    children: tuple[OutlineNode, ...] = ()


@dataclass(frozen=True)
class ContainerCandidate:
    """Sibling elements sharing a signature: a likely record list."""

    selector: str
    count: int
    samples: tuple[OutlineNode, ...]


@dataclass(frozen=True)
class PageOutline:
    title: str = ""
    headings: tuple[str, ...] = ()
    jsonld_types: tuple[str, ...] = ()
    value_attributes: tuple[tuple[str, int], ...] = ()
    containers: tuple[ContainerCandidate, ...] = field(default_factory=tuple)
