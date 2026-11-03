"""Synchronous repository ports; async callers must offload storage operations."""

from datetime import datetime
from typing import Protocol

from rastro.domain.lifecycle import RecipeState
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import RecipeKey, StoredRecipe
from rastro.domain.models import Recipe


class RecipeRepository(Protocol):
    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None: ...

    def save_success(
        self,
        key: RecipeKey,
        recipe: Recipe,
        *,
        selected_index: int,
        at: datetime,
        candidate: bool = False,
    ) -> StoredRecipe: ...

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int: ...

    def prune(self, *, before: datetime) -> int: ...


class RecipeLifecycle(Protocol):
    """Lifecycle control used by repair, drift and operators (REC-005, REPAIR-006)."""

    def transition(
        self, key: RecipeKey, version: int, state: RecipeState, *, reason: str, at: datetime
    ) -> StoredRecipe: ...

    def rollback(
        self, key: RecipeKey, version: int, *, reason: str, at: datetime
    ) -> StoredRecipe: ...

    def history(self, key: RecipeKey) -> LifecycleHistory: ...
