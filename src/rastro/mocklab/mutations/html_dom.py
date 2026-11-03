"""A small lxml seam for HTML mutations: bounded parsing, CSS queries, tree edits.

lxml and cssselect are already installed with Scrapling; Mock Lab composes them at the
edge, like the rest of the package (ADR 0014). Domain code never imports this module.
"""

from __future__ import annotations

import copy
import re
from typing import Any, cast

from cssselect import HTMLTranslator, SelectorError
from lxml import etree  # type: ignore[import-untyped]
from lxml import html as lhtml

from rastro.mocklab.errors import MockLabError
from rastro.mocklab.mutations.limits import check_bytes

type Node = Any
"""An lxml element; lxml ships no type information."""

_TRANSLATOR = HTMLTranslator()
_DOCUMENT_TAGS = frozenset({"html", "head", "body"})
_XML_DECLARATION = re.compile(r"\A\s*<\?xml[^>]*\?>")


def css_selector(selector: str) -> str:
    """Pydantic validator: reject blank or unparsable selectors at configuration time."""
    if not selector.strip():
        raise ValueError("CSS selector must not be blank")
    try:
        _TRANSLATOR.css_to_xpath(selector)
    except SelectorError:
        raise ValueError("Invalid CSS selector") from None
    return selector


class HtmlDom:
    """A parsed document that can be queried, edited in place and serialized."""

    def __init__(self, root: Node) -> None:
        self._root = root

    @classmethod
    def parse(cls, document: str) -> HtmlDom | None:
        """Parse within ``Limits.max_html_bytes``; ``None`` when there is no element.

        A leading XML declaration (XHTML) is dropped: lxml refuses it on text input.
        """
        check_bytes(document, "html")
        cleaned = _XML_DECLARATION.sub("", document.replace("\x00", ""), count=1)
        if not cleaned.strip():
            return None
        parser = lhtml.HTMLParser(remove_comments=False, default_doctype=False)
        try:
            root = lhtml.document_fromstring(cleaned, parser=parser)
        except etree.ParserError:
            return None
        except ValueError:
            raise MockLabError("HTML document cannot be parsed") from None
        return cls(root)

    def select(self, selector: str) -> list[Node]:
        """Matching elements in document order."""
        matches = cast(list[Node], self._root.xpath(_TRANSLATOR.css_to_xpath(selector)))
        return [node for node in matches if isinstance(node.tag, str)]

    def attached(self, node: Node) -> bool:
        """False once an earlier edit removed ``node`` or one of its ancestors."""
        return node is self._root or any(a is self._root for a in node.iterancestors())

    def path(self, node: Node) -> str:
        return str(etree.ElementTree(self._root).getpath(node))

    def serialize(self) -> str:
        """The whole tree: doctype and comments outside the root element included."""
        tree = self._root.getroottree()
        return str(etree.tostring(tree, method="html", encoding="unicode"))


def stable(document: str) -> bool:
    """True when reparsing ``document`` yields the same markup.

    libxml2 repairs invalid nesting (a ``<p>`` around a ``<div>``, an ``<a>`` inside an
    ``<a>``) on the next parse; such an edit would be reported but never seen.
    """
    reparsed = HtmlDom.parse(document)
    return reparsed is not None and reparsed.serialize() == document


def is_document_level(node: Node) -> bool:
    return str(node.tag).lower() in _DOCUMENT_TAGS or node.getparent() is None


def element_children(node: Node) -> list[Node]:
    return [child for child in node if isinstance(child.tag, str)]


def classes(node: Node) -> list[str]:
    return str(node.get("class", "")).split()


def set_classes(node: Node, tokens: list[str]) -> None:
    if tokens:
        node.set("class", " ".join(tokens))
    else:
        node.attrib.pop("class", None)


def reorder_children(container: Node, order: list[int]) -> None:
    """Permute element children; text and comments keep their slots."""
    nodes = list(container)
    slots = [index for index, child in enumerate(nodes) if isinstance(child.tag, str)]
    elements = [nodes[index] for index in slots]
    tails = [element.tail for element in elements]
    for slot, source, tail in zip(slots, order, tails, strict=True):
        nodes[slot] = elements[source]
        nodes[slot].tail = tail
    for child in list(container):
        container.remove(child)
    container.extend(nodes)


def wrap(node: Node, tag: str, css_class: str | None) -> Node:
    parent = node.getparent()
    wrapper = lhtml.Element(tag)
    if css_class:
        wrapper.set("class", css_class)
    wrapper.tail, node.tail = node.tail, None
    parent.replace(node, wrapper)
    wrapper.append(node)
    return wrapper


def duplicate(node: Node, copies: int) -> list[Node]:
    """Insert ``copies`` deep copies right after ``node``; return them in order."""
    parent = node.getparent()
    index = parent.index(node)
    clones = [copy.deepcopy(node) for _ in range(copies)]
    for offset, clone in enumerate(clones, start=1):
        parent.insert(index + offset, clone)
    return clones


def remove(node: Node) -> None:
    """Detach ``node`` and keep its tail text in the document."""
    parent, tail = node.getparent(), node.tail or ""
    previous = node.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(node)


def clear(node: Node) -> None:
    """Drop children and text; attributes and tail stay."""
    for child in list(node):
        node.remove(child)
    node.text = None
