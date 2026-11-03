"""The shared apply loops for JSON operators: one per field, or one container."""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from rastro.domain.json import MISSING, resolve_pointer
from rastro.domain.sources import Representation
from rastro.mocklab.mutations.contracts import (
    MaxTargets,
    MutationChange,
    MutationResult,
    OperatorBase,
)
from rastro.mocklab.mutations.json_doc import (
    FieldPointer,
    Pointer,
    Record,
    decode,
    encode,
    field_slot,
)
from rastro.mocklab.mutations.json_doc import records as find_records
from rastro.mocklab.mutations.rng import sample_indices
from rastro.mocklab.seeds import DeterministicStream


class JsonFieldOperator(OperatorBase):
    """Edits ``field`` in each object record found at ``records``.

    ``records`` points at one object or an array of objects (non-objects are ignored);
    ``field`` is a pointer inside each record. Records lacking the field mean
    ``target_not_found``; fields whose value does not qualify, ``no_eligible_target``.
    """

    representation: ClassVar[Representation] = "json"

    records: Pointer = ""
    field: FieldPointer
    max_targets: MaxTargets = None

    def apply(self, document: str, stream: DeterministicStream) -> MutationResult:
        payload, reason = decode(document)
        if reason is not None:
            return self.skipped(document, reason)
        slots = self._slots(payload)
        if not slots:
            return self.skipped(document, "target_not_found")
        eligible = [slot for slot in slots if self.eligible(slot[1], slot[2])]
        if not eligible:
            return self.skipped(document, "no_eligible_target")
        chosen = sample_indices(stream, len(eligible), self.max_targets)
        changes = [self.edit(*eligible[index], stream) for index in chosen]
        return self.applied(encode(payload), changes)

    def _slots(self, payload: object) -> list[tuple[str, Record, str]]:
        found: list[tuple[str, Record, str]] = []
        for path, record in find_records(payload, self.records) or []:
            slot = field_slot(record, self.field)
            if slot is not None:
                found.append((path + self.field, *slot))
        return found

    def eligible(self, parent: Record, key: str) -> bool:
        return True

    @abstractmethod
    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        """Mutate ``parent[key]`` (absolute pointer ``path``) and describe the change."""
        ...

    def change(
        self, path: str, path_after: str | None, before: object, after: object
    ) -> MutationChange:
        """Values are recorded JSON-encoded; ``MISSING`` (a deleted value) becomes ``None``."""
        return MutationChange(
            target=self.records + self.field,
            path_before=path,
            path_after=path_after,
            before=None if before is MISSING else encode(before),
            after=None if after is MISSING else encode(after),
        )


class JsonContainerOperator(OperatorBase):
    """Edits the array or object at ``pointer``."""

    representation: ClassVar[Representation] = "json"

    pointer: Pointer = ""

    def apply(self, document: str, stream: DeterministicStream) -> MutationResult:
        payload, reason = decode(document)
        if reason is not None:
            return self.skipped(document, reason)
        container = resolve_pointer(payload, self.pointer)
        if not isinstance(container, list | dict):
            return self.skipped(document, "target_not_found")
        if not self.eligible(container):
            return self.skipped(document, "no_eligible_target")
        changes = self.edit(container, stream)
        return self.applied(encode(payload), changes)

    @abstractmethod
    def eligible(self, container: list[object] | Record) -> bool:
        """Whether ``edit`` would change ``container``."""
        ...

    @abstractmethod
    def edit(
        self, container: list[object] | Record, stream: DeterministicStream
    ) -> list[MutationChange]:
        """Mutate ``container`` in place and describe each change."""
        ...

    def change(self, path: str, path_after: str, before: str, after: str) -> MutationChange:
        return MutationChange(
            target=self.pointer, path_before=path, path_after=path_after, before=before, after=after
        )
