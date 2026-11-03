"""Synchronous repository ports; async callers must offload storage operations."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from rastro.domain.lifecycle import RecipeState
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import RecipeKey, StoredRecipe
from rastro.domain.models import Recipe


class RecipeRepository(Protocol):
    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None: ...

    def save_success(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe: ...

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int: ...

    def prune(self, *, before: datetime) -> int: ...


@runtime_checkable
class RecipeLifecycle(Protocol):
    """Lifecycle control used by repair, drift and operators (REC-005, REPAIR-006).

    Stores that implement only ``RecipeRepository`` hold no candidate or retired
    rows, so learning on them stores version 1 exactly as in 0.1.0.
    """

    def next_version(self, key: RecipeKey) -> int:
        """One above the highest stored version of ``key`` in any state, or 1."""
        ...

    def save_candidate(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe: ...

    def transition(
        self, key: RecipeKey, version: int, state: RecipeState, *, reason: str, at: datetime
    ) -> StoredRecipe: ...

    def rollback(
        self, key: RecipeKey, version: int, *, reason: str, at: datetime
    ) -> StoredRecipe: ...

    def history(self, key: RecipeKey) -> LifecycleHistory: ...
