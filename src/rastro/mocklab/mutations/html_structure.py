"""HTML operators that move, wrap, copy or empty elements (MOCK-002)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, Literal

from pydantic import Field

from rastro.mocklab.mutations import html_dom
from rastro.mocklab.mutations.contracts import MutationCategory
from rastro.mocklab.mutations.html_base import ClassToken, Edit, HtmlOperator
from rastro.mocklab.mutations.html_dom import Node
from rastro.mocklab.mutations.rng import non_identity_permutation
from rastro.mocklab.seeds import DeterministicStream

type WrapperTag = Literal["div", "section", "article", "span", "main", "aside", "header", "footer"]
"""Flow containers; libxml2 and HTML5 parsers keep them where wrapping puts them."""

_FIXED_PARENT = frozenset(
    {
        "li",
        "dt",
        "dd",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "tfoot",
        "caption",
        "col",
        "colgroup",
        "option",
        "optgroup",
        "title",
        "meta",
        "link",
        "base",
        "style",
    }
)
"""Elements whose parent is prescribed; a wrapper would be dropped or moved by parsers."""


def _slots(children: list[Node], order: Iterable[int]) -> str:
    """Children as ``<original index>:<tag>``, in document order."""
    return ",".join(f"{index}:{children[index].tag}" for index in order)


class HtmlReorderChildren(HtmlOperator):
    """Permute the element children of each matched container; at least one moves."""

    operator: Literal["html_reorder_children"] = "html_reorder_children"
    category: ClassVar[MutationCategory] = "dom_reordering"

    def eligible(self, node: Node) -> bool:
        return len(html_dom.element_children(node)) >= 2

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        children = html_dom.element_children(node)
        order = non_identity_permutation(stream, len(children))
        html_dom.reorder_children(node, order)
        return Edit(node, _slots(children, range(len(children))), _slots(children, order))


class HtmlWrapElement(HtmlOperator):
    """Wrap each matched element in a new parent element."""

    operator: Literal["html_wrap_element"] = "html_wrap_element"
    category: ClassVar[MutationCategory] = "element_wrapping"
    structural_only: ClassVar[bool] = True

    wrapper_tag: WrapperTag = "div"
    wrapper_class: ClassToken | None = None

    def eligible(self, node: Node) -> bool:
        return str(node.tag).lower() not in _FIXED_PARENT

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        wrapper = html_dom.wrap(node, self.wrapper_tag, self.wrapper_class)
        return Edit(node, None, "wrapper=", (wrapper,))


class HtmlDuplicateElement(HtmlOperator):
    """Insert copies of each matched element directly after it."""

    operator: Literal["html_duplicate_element"] = "html_duplicate_element"
    category: ClassVar[MutationCategory] = "duplicate_elements"
    structural_only: ClassVar[bool] = True

    copies: int = Field(default=1, ge=1, le=20)

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        clones = html_dom.duplicate(node, self.copies)
        return Edit(node, None, "copies=", tuple(clones))


class HtmlEmptyContainer(HtmlOperator):
    """Remove all children and text of each matched, non-empty element."""

    operator: Literal["html_empty_container"] = "html_empty_container"
    category: ClassVar[MutationCategory] = "empty_containers"

    def eligible(self, node: Node) -> bool:
        return len(node) > 0 or bool(node.text and node.text.strip())

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        before = f"children={len(node)},text={bool(node.text and node.text.strip())}"
        html_dom.clear(node)
        return Edit(node, before, "children=0,text=False")
