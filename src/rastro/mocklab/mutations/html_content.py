"""HTML operators that change classes, labels, fields and pagination (MOCK-002)."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, model_validator

from rastro.mocklab.mutations import html_dom
from rastro.mocklab.mutations.contracts import MutationCategory
from rastro.mocklab.mutations.html_base import ClassToken, CssSelector, Edit, HtmlOperator
from rastro.mocklab.mutations.html_dom import Node
from rastro.mocklab.seeds import DeterministicStream

type AttributeName = Annotated[str, Field(pattern=r"^[a-z_:][a-z0-9_.:-]{0,63}$")]
"""Lowercase, as lxml stores HTML attribute names."""


class HtmlChangeClasses(HtmlOperator):
    """Rename, remove or add class tokens; ``hash_suffix`` mimics CSS-module hash churn."""

    operator: Literal["html_change_classes"] = "html_change_classes"
    category: ClassVar[MutationCategory] = "class_changes"

    rename: dict[ClassToken, ClassToken] = Field(default_factory=dict, max_length=32)
    remove: tuple[ClassToken, ...] = ()
    add: tuple[ClassToken, ...] = ()
    hash_suffix: bool = False

    @model_validator(mode="after")
    def _has_an_edit(self) -> Self:
        if not (self.rename or self.remove or self.add or self.hash_suffix):
            raise ValueError("Configure rename, remove, add or hash_suffix")
        return self

    def eligible(self, node: Node) -> bool:
        current = html_dom.classes(node)
        return self._next(current, "") != current or (self.hash_suffix and bool(current))

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        current = html_dom.classes(node)
        suffix = f"-{stream.letters(6)}" if self.hash_suffix else ""
        updated = self._next(current, suffix)
        html_dom.set_classes(node, updated)
        return Edit(node, " ".join(current), " ".join(updated))

    def _next(self, current: list[str], suffix: str) -> list[str]:
        """Only configured edits: existing duplicate tokens are kept as they are."""
        tokens = [self.rename.get(t, t) + suffix for t in current if t not in self.remove]
        return tokens + [token for token in self.add if token not in tokens]


class HtmlRemoveField(HtmlOperator):
    """Remove matched elements, or only ``attribute`` from them."""

    operator: Literal["html_remove_field"] = "html_remove_field"
    category: ClassVar[MutationCategory] = "missing_fields"

    attribute: AttributeName | None = None

    def eligible(self, node: Node) -> bool:
        if self.attribute is None:
            return not html_dom.is_document_level(node)
        return self.attribute in node.attrib

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        if self.attribute is None:
            html_dom.remove(node)
            return Edit(None, str(node.tag), None)
        value = node.attrib.pop(self.attribute)
        return Edit(node, f"{self.attribute}={value}", None)


class HtmlChangeLabel(HtmlOperator):
    """Rewrite an element's own text, or an attribute such as ``aria-label``.

    With ``match`` only values containing it change, and only that substring is replaced.
    """

    operator: Literal["html_change_label"] = "html_change_label"
    category: ClassVar[MutationCategory] = "changed_labels"

    text: str = Field(max_length=512)
    match: str | None = Field(default=None, min_length=1, max_length=512)
    attribute: AttributeName | None = None

    def eligible(self, node: Node) -> bool:
        current = self._current(node)
        return current is not None and self._replaced(current) != current

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        current = self._current(node) or ""
        updated = self._replaced(current)
        if self.attribute is None:
            node.text = updated
        else:
            node.set(self.attribute, updated)
        return Edit(node, current, updated)

    def _current(self, node: Node) -> str | None:
        if self.attribute is None:
            return str(node.text or "")
        value = node.get(self.attribute)
        return None if value is None else str(value)

    def _replaced(self, current: str) -> str:
        if self.match is None:
            return self.text
        return current.replace(self.match, self.text) if self.match in current else current


class HtmlModifyPagination(HtmlOperator):
    """Change pagination controls: rewrite ``href``, disable the link, or remove it.

    Rewritten links use ``href_template`` with ``{token}`` replaced by seeded letters.
    """

    operator: Literal["html_modify_pagination"] = "html_modify_pagination"
    category: ClassVar[MutationCategory] = "modified_pagination"

    selector: CssSelector = "a[rel~=next], .pagination a, .pager a"
    mode: Literal["rewrite_href", "disable", "remove"] = "rewrite_href"
    href_template: str = Field(default="?cursor={token}", min_length=1, max_length=512)

    def eligible(self, node: Node) -> bool:
        if self.mode == "remove":
            return not html_dom.is_document_level(node)
        href = node.get("href")
        fixed = self.mode == "rewrite_href" and "{token}" not in self.href_template
        return href is not None and not (fixed and href == self.href_template)

    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        before = f"href={node.get('href')}"
        if self.mode == "remove":
            html_dom.remove(node)
            return Edit(None, before, None)
        if self.mode == "disable":
            del node.attrib["href"]
            node.set("aria-disabled", "true")
            return Edit(node, before, "aria-disabled=true")
        updated = self.href_template.replace("{token}", stream.letters(8))
        node.set("href", updated)
        return Edit(node, before, f"href={updated}")
