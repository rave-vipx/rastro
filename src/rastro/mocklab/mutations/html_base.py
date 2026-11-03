"""The shared apply loop for HTML operators: parse, select, sample, edit, serialize."""

from __future__ import annotations

from abc import abstractmethod
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from rastro.domain.sources import Representation
from rastro.mocklab.mutations.contracts import (
    MaxTargets,
    MutationChange,
    MutationResult,
    OperatorBase,
)
from rastro.mocklab.mutations.html_dom import HtmlDom, Node, css_selector, is_document_level
from rastro.mocklab.mutations.rng import sample_indices
from rastro.mocklab.seeds import DeterministicStream

type CssSelector = Annotated[
    str, Field(min_length=1, max_length=1024), AfterValidator(css_selector)
]
type ClassToken = Annotated[str, Field(pattern=r"^[A-Za-z_-][A-Za-z0-9_-]{0,127}$")]


class HtmlOperator(OperatorBase):
    """Subclasses define which matched elements qualify and how one is edited.

    Matched elements are filtered by ``eligible``; ``max_targets`` of them are sampled
    by the seeded stream. An element detached by an earlier edit in the same pass is
    skipped. When nothing qualifies, the outcome is ``no_applicable_target`` and the
    document is returned unchanged.
    """

    representation: ClassVar[Representation] = "html"
    structural_only: ClassVar[bool] = False
    """True when the edit needs a parent, so ``html``, ``head`` and ``body`` never qualify."""

    selector: CssSelector
    max_targets: MaxTargets = None

    def apply(self, document: str, stream: DeterministicStream) -> MutationResult:
        dom = HtmlDom.parse(document)
        if dom is None:
            return self.skipped(document, "empty_document")
        matched = dom.select(self.selector)
        if not matched:
            return self.skipped(document, "target_not_found")
        eligible = [node for node in matched if self._qualifies(node)]
        if not eligible:
            return self.skipped(document, "no_eligible_target")
        changes: list[MutationChange] = []
        for index in sample_indices(stream, len(eligible), self.max_targets):
            node = eligible[index]
            if dom.attached(node):
                changes.append(self.edit(dom, node, stream))
        return self.applied(dom.serialize(), changes)

    def _qualifies(self, node: Node) -> bool:
        return not (self.structural_only and is_document_level(node)) and self.eligible(node)

    def eligible(self, node: Node) -> bool:
        return True

    @abstractmethod
    def edit(self, dom: HtmlDom, node: Node, stream: DeterministicStream) -> MutationChange:
        """Mutate one eligible, attached element and describe the change."""
        ...

    def change(
        self, dom: HtmlDom, node: Node, path_before: str, before: str | None, after: str | None
    ) -> MutationChange:
        """A change record for an element that is still in the document."""
        return MutationChange(
            target=self.selector,
            path_before=path_before,
            path_after=dom.path(node),
            before=before,
            after=after,
        )

    def removal(self, path_before: str, before: str | None) -> MutationChange:
        """A change record for an element that left the document."""
        return MutationChange(
            target=self.selector,
            path_before=path_before,
            path_after=None,
            before=before,
            after=None,
        )
