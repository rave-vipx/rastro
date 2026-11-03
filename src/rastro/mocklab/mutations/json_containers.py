"""JSON operators that reorder, duplicate or empty one array or object (MOCK-002)."""

from __future__ import annotations

import copy
from typing import ClassVar, Literal

from pydantic import Field

from rastro.mocklab.mutations.contracts import MaxTargets, MutationCategory, MutationChange
from rastro.mocklab.mutations.json_base import JsonContainerOperator
from rastro.mocklab.mutations.json_doc import Record
from rastro.mocklab.mutations.rng import non_identity_permutation, sample_indices
from rastro.mocklab.seeds import DeterministicStream


class JsonReorder(JsonContainerOperator):
    """Permute array items, or object keys; at least one position changes."""

    operator: Literal["json_reorder"] = "json_reorder"
    category: ClassVar[MutationCategory] = "dom_reordering"

    def eligible(self, container: list[object] | Record) -> bool:
        return len(container) >= 2

    def edit(
        self, container: list[object] | Record, stream: DeterministicStream
    ) -> list[MutationChange]:
        order = non_identity_permutation(stream, len(container))
        after = "order=" + ",".join(str(index) for index in order)
        if isinstance(container, list):
            container[:] = [container[index] for index in order]
            return [self.change(self.pointer, self.pointer, f"items={len(order)}", after)]
        keys = list(container)
        reordered = {keys[index]: container[keys[index]] for index in order}
        container.clear()
        container.update(reordered)
        return [self.change(self.pointer, self.pointer, ",".join(keys), after)]


class JsonDuplicateItem(JsonContainerOperator):
    """Insert copies of array items right after them."""

    operator: Literal["json_duplicate_item"] = "json_duplicate_item"
    category: ClassVar[MutationCategory] = "duplicate_elements"

    copies: int = Field(default=1, ge=1, le=20)
    max_targets: MaxTargets = 1

    def eligible(self, container: list[object] | Record) -> bool:
        return isinstance(container, list) and bool(container)

    def edit(
        self, container: list[object] | Record, stream: DeterministicStream
    ) -> list[MutationChange]:
        assert isinstance(container, list)  # guaranteed by eligible
        changes: list[MutationChange] = []
        chosen = sample_indices(stream, len(container), self.max_targets)
        for shift, index in enumerate(chosen):
            live = index + shift * self.copies
            container[live + 1 : live + 1] = [
                copy.deepcopy(container[live]) for _ in range(self.copies)
            ]
            path, count = f"{self.pointer}/{index}", f"count={1 + self.copies}"
            changes.append(self.change(path, f"{self.pointer}/{live}", "count=1", count))
        return changes


class JsonEmptyContainer(JsonContainerOperator):
    """Remove every item or key of the container."""

    operator: Literal["json_empty_container"] = "json_empty_container"
    category: ClassVar[MutationCategory] = "empty_containers"

    def eligible(self, container: list[object] | Record) -> bool:
        return bool(container)

    def edit(
        self, container: list[object] | Record, stream: DeterministicStream
    ) -> list[MutationChange]:
        before = f"size={len(container)}"
        container.clear()
        return [self.change(self.pointer, self.pointer, before, "size=0")]
