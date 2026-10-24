"""Synchronous repository port; async callers must offload storage operations."""

from datetime import datetime
from typing import Protocol

from rastro.domain.memory import RecipeKey, StoredRecipe
from rastro.domain.models import Recipe


class RecipeRepository(Protocol):
    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None: ...

    def save_success(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe: ...

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int: ...

    def prune(self, *, before: datetime) -> int: ...
