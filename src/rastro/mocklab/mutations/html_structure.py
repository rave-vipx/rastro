"""HTML operators that move, wrap, copy or empty elements (MOCK-002)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from rastro.mocklab.mutations import html_dom
from rastro.mocklab.mutations.contracts import MutationCategory, MutationChange
from rastro.mocklab.mutations.html_base import ClassToken, HtmlOperator
from rastro.mocklab.mutations.html_dom import HtmlDom, Node
from rastro.mocklab.mutations.rng import non_identity_permutation
from rastro.mocklab.seeds import DeterministicStream


def _tags(nodes: list[Node]) -> str:
    return ",".join(str(node.tag) for node in nodes)


class HtmlReorderChildren(HtmlOperator):
    """Permute the element children of each matched container; at least one moves."""

    operator: Literal["html_reorder_children"] = "html_reorder_children"
    category: ClassVar[MutationCategory] = "dom_reordering"

    def eligible(self, node: Node) -> bool:
        return len(html_dom.element_children(node)) >= 2

    def edit(self, dom: HtmlDom, node: Node, stream: DeterministicStream) -> MutationChange:
        path, children = dom.path(node), html_dom.element_children(node)
        order = non_identity_permutation(stream, len(children))
        html_dom.reorder_children(node, order)
        after = ",".join(str(index) for index in order)
        return self.change(dom, node, path, _tags(children), f"order={after}")


class HtmlWrapElement(HtmlOperator):
    """Wrap each matched element in a new parent element."""

    operator: Literal["html_wrap_element"] = "html_wrap_element"
    category: ClassVar[MutationCategory] = "element_wrapping"
    structural_only: ClassVar[bool] = True

    wrapper_tag: str = Field(default="div", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    wrapper_class: ClassToken | None = None

    def edit(self, dom: HtmlDom, node: Node, stream: DeterministicStream) -> MutationChange:
        path = dom.path(node)
        wrapper = html_dom.wrap(node, self.wrapper_tag, self.wrapper_class)
        return self.change(dom, node, path, None, f"wrapper={dom.path(wrapper)}")


class HtmlDuplicateElement(HtmlOperator):
    """Insert copies of each matched element directly after it."""

    operator: Literal["html_duplicate_element"] = "html_duplicate_element"
    category: ClassVar[MutationCategory] = "duplicate_elements"
    structural_only: ClassVar[bool] = True

    copies: int = Field(default=1, ge=1, le=20)

    def edit(self, dom: HtmlDom, node: Node, stream: DeterministicStream) -> MutationChange:
        path = dom.path(node)
        clones = html_dom.duplicate(node, self.copies)
        after = ",".join(dom.path(clone) for clone in clones)
        return self.change(dom, node, path, None, f"copies={after}")


class HtmlEmptyContainer(HtmlOperator):
    """Remove all children and text of each matched, non-empty element."""

    operator: Literal["html_empty_container"] = "html_empty_container"
    category: ClassVar[MutationCategory] = "empty_containers"

    def eligible(self, node: Node) -> bool:
        return len(node) > 0 or bool(node.text and node.text.strip())

    def edit(self, dom: HtmlDom, node: Node, stream: DeterministicStream) -> MutationChange:
        path, before = dom.path(node), f"children={len(node)}"
        html_dom.clear(node)
        return self.change(dom, node, path, before, "children=0")
