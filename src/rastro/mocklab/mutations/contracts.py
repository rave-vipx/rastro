"""Outcome contracts shared by every Mock Lab mutation operator (MOCK-002)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rastro.domain.sources import Representation

type MutationCategory = Literal[
    "dom_reordering",
    "class_changes",
    "element_wrapping",
    "missing_fields",
    "duplicate_elements",
    "empty_containers",
    "changed_labels",
    "modified_pagination",
]

type SkipReason = Literal[
    "empty_document",
    "invalid_document",
    "target_not_found",
    "no_eligible_target",
    "unstable_markup",
]
"""Why an operator found nothing to change; each is an explicit, non-success outcome."""

type MaxTargets = Annotated[int | None, Field(ge=1, le=10_000)]
"""How many eligible targets to edit; ``None`` edits all, fewer are sampled by seed."""


class MutationStatus(StrEnum):
    APPLIED = "applied"
    NO_APPLICABLE_TARGET = "no_applicable_target"


class StrictContract(BaseModel):
    """Frozen, strict model: no coercion, no unknown keys."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MutationChange(StrictContract):
    """One machine-readable modification.

    ``target`` is the configured selector or pointer. Paths are XPath for HTML and
    RFC 6901 pointers for JSON; ``path_after`` is ``None`` when the node was removed.
    ``before`` and ``after`` summarize the changed property (classes, text, a key...).
    """

    target: str
    path_before: str
    path_after: str | None
    before: str | None
    after: str | None


class MutationResult(StrictContract):
    """What one operator did to one document."""

    status: MutationStatus
    operator: str
    category: MutationCategory
    representation: Representation
    document: str
    changes: tuple[MutationChange, ...] = ()
    reason: SkipReason | None = None

    @model_validator(mode="after")
    def _outcome_is_explicit(self) -> Self:
        applied = self.status is MutationStatus.APPLIED
        if applied != bool(self.changes) or applied == (self.reason is not None):
            raise ValueError("applied results need changes; skipped results need a reason")
        return self


class OperatorBase(StrictContract):
    """Metadata and outcome constructors for concrete operators."""

    operator: str
    category: ClassVar[MutationCategory]
    representation: ClassVar[Representation]

    def applied(self, document: str, changes: list[MutationChange]) -> MutationResult:
        return self._result(MutationStatus.APPLIED, document, tuple(changes), None)

    def skipped(self, document: str, reason: SkipReason) -> MutationResult:
        return self._result(MutationStatus.NO_APPLICABLE_TARGET, document, (), reason)

    def _result(
        self,
        status: MutationStatus,
        document: str,
        changes: tuple[MutationChange, ...],
        reason: SkipReason | None,
    ) -> MutationResult:
        return MutationResult(
            status=status,
            operator=self.operator,
            category=self.category,
            representation=self.representation,
            document=document,
            changes=changes,
            reason=reason,
        )
