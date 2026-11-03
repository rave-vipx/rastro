"""A store written against the 0.1.0 four-method RecipeRepository port keeps working."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel

from rastro import Outcome, Rastro, Source
from rastro.domain.memory import RecipeKey, RecipeMetadata, StoredRecipe
from rastro.domain.models import Recipe
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository


class DictRepo:
    """Exactly the 0.1.0 port: get, save_success, delete and prune."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], StoredRecipe] = {}

    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None:
        matches = [
            stored
            for (identity, number), stored in self.rows.items()
            if identity == key.canonical() and version in (None, number)
        ]
        return max(matches, key=lambda stored: stored.recipe.version, default=None)

    def save_success(
        self, key: RecipeKey, recipe: Recipe, *, selected_index: int, at: datetime
    ) -> StoredRecipe:
        slot = (key.canonical(), recipe.version)
        previous = self.rows.get(slot)
        runs = 1 if previous is None else previous.metadata.successful_runs + 1
        created = at if previous is None else previous.metadata.created_at
        stored = StoredRecipe(
            key=key,
            recipe=recipe,
            metadata=RecipeMetadata(
                selected_index=selected_index,
                created_at=created,
                last_success_at=at,
                successful_runs=runs,
            ),
        )
        self.rows[slot] = stored
        return stored

    def delete(self, key: RecipeKey, *, version: int | None = None) -> int:
        doomed = [slot for slot in self.rows if slot[0] == key.canonical()]
        doomed = [slot for slot in doomed if version in (None, slot[1])]
        for slot in doomed:
            del self.rows[slot]
        return len(doomed)

    def prune(self, *, before: datetime) -> int:
        doomed = [s for s, row in self.rows.items() if row.metadata.last_success_at < before]
        for slot in doomed:
            del self.rows[slot]
        return len(doomed)


class Product(BaseModel):
    name: str


PAGE = '<script type="application/ld+json">{"name": "Notebook"}</script>'
SOURCE = Source(url="https://example.com/products", locale="en-US")


def test_four_method_store_satisfies_only_the_repository_port() -> None:
    repository: RecipeRepository = DictRepo()  # mypy checks protocol conformance here.
    assert not isinstance(repository, RecipeLifecycle)


@pytest.mark.asyncio
async def test_four_method_store_learns_then_reuses_at_version_1() -> None:
    repository = DictRepo()
    engine = Rastro(repository=repository, discover_recipes=True)
    learned = await engine.extract_html_async(PAGE, source=SOURCE, schema=Product)
    assert learned.status == Outcome.SUCCESS and not learned.recipe_reused
    assert learned.recipe_version == 1
    assert [version for _, version in repository.rows] == [1]
    reused = await engine.extract_html_async(PAGE, source=SOURCE, schema=Product)
    assert reused.status == Outcome.SUCCESS and reused.recipe_reused
    assert reused.records[0].data == learned.records[0].data
