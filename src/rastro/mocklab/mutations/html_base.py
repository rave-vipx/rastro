"""The shared apply loop for HTML operators: parse, select, sample, edit, serialize."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from rastro.domain.sources import Representation
from rastro.mocklab.mutations.contracts import (
    MaxTargets,
    MutationChange,
    MutationResult,
    OperatorBase,
)
from rastro.mocklab.mutations.html_dom import (
    HtmlDom,
    Node,
    css_selector,
    is_document_level,
    stable,
)
from rastro.mocklab.mutations.rng import sample_indices
from rastro.mocklab.seeds import DeterministicStream

type CssSelector = Annotated[
    str, Field(min_length=1, max_length=1024), AfterValidator(css_selector)
]
type ClassToken = Annotated[str, Field(pattern=r"^[A-Za-z_-][A-Za-z0-9_-]{0,127}$")]


@dataclass(frozen=True)
class Edit:
    """What one ``edit`` did; paths are resolved only after the whole pass.

    ``node`` is the element whose final path becomes ``path_after`` (``None`` when it
    left the document). ``after_nodes`` are new elements (a wrapper, copies) whose final
    paths are appended to ``after``.
    """

    node: Node | None
    before: str | None
    after: str | None
    after_nodes: tuple[Node, ...] = ()


class HtmlOperator(OperatorBase):
    """Subclasses define which matched elements qualify and how one is edited.

    Matched elements are filtered by ``eligible``; ``max_targets`` of them are sampled
    by the seeded stream. Every chosen element's path is recorded before any edit, and
    paths after the edit are resolved once all edits are done, so change records refer
    to the input and the output documents, never to a half-edited tree. An element
    detached by an earlier edit in the same pass is skipped. When nothing qualifies, or
    the edited markup would not survive reparsing, the outcome is
    ``no_applicable_target`` and the document is returned unchanged.
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
        chosen = [eligible[i] for i in sample_indices(stream, len(eligible), self.max_targets)]
        paths = [dom.path(node) for node in chosen]
        edits = [
            (path, self.edit(node, stream))
            for path, node in zip(paths, chosen, strict=True)
            if dom.attached(node)
        ]
        output = dom.serialize()
        if not stable(output):
            return self.skipped(document, "unstable_markup")
        return self.applied(output, [self._record(dom, path, edit) for path, edit in edits])

    def _qualifies(self, node: Node) -> bool:
        return not (self.structural_only and is_document_level(node)) and self.eligible(node)

    def _record(self, dom: HtmlDom, path_before: str, edit: Edit) -> MutationChange:
        def final(node: Node) -> str | None:
            return dom.path(node) if dom.attached(node) else None

        node_path = None if edit.node is None else final(edit.node)
        extra = ",".join(path for path in map(final, edit.after_nodes) if path)
        after = edit.after if edit.after is None or not extra else edit.after + extra
        return MutationChange(
            target=self.selector,
            path_before=path_before,
            path_after=node_path,
            before=edit.before,
            after=after,
        )

    def eligible(self, node: Node) -> bool:
        return True

    @abstractmethod
    def edit(self, node: Node, stream: DeterministicStream) -> Edit:
        """Mutate one eligible, attached element and describe the change."""
        ...
