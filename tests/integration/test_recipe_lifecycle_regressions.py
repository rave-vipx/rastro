"""Regression tests for the independent PR #46 review, using the reviewer's inputs."""

import inspect
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rastro import CssRecipe, Outcome, Rastro
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeConflictError, RecipeKey, RecipeStorageError
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository

NOW = datetime(2026, 1, 2, tzinfo=UTC)
BAD_CSS_V1 = CssRecipe(
    record_selector="div.missing", fields={"name": "h1::text", "price": ".price::text"}
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "recipes.db"


@pytest.fixture
def repo(path) -> SQLiteRecipeRepository:
    return SQLiteRecipeRepository(path)


@pytest.fixture
def key(source, schema) -> RecipeKey:
    return RecipeKey.for_source(source, schema)


def test_finding_1_candidate_cannot_mutate_an_existing_active_version(repo, key):
    wrong = CssRecipe(
        record_selector="div.distractor",
        fields={"name": ".bad-name::text", "price": ".bad-price::text"},
    )
    active = CssRecipe(
        record_selector="article.product",
        fields={"name": "h1::text", "price": ".price::text"},
        fallbacks=(wrong,),
    )
    repo.save_success(key, active, selected_index=0, at=NOW)
    with pytest.raises(RecipeConflictError, match="new recipe version"):
        repo.save_candidate(key, active, selected_index=1, at=NOW + timedelta(hours=1))
    served = repo.get(key)
    assert served.metadata.state is RecipeState.ACTIVE
    assert served.metadata.selected_index == 0 and served.metadata.successful_runs == 1
    assert len(repo.history(key).events) == 1


@pytest.mark.asyncio
async def test_finding_2a_learning_after_rejected_candidate_uses_a_new_version(
    repo, key, html, source, schema
):
    repo.save_candidate(key, BAD_CSS_V1, selected_index=0, at=NOW)
    repo.transition(key, 1, RecipeState.RETIRED, reason="Failed fixture check", at=NOW)
    engine = Rastro(repository=repo, discover_recipes=True)
    result = await engine.extract_html_async(html, source=source, schema=schema)
    assert result.status == Outcome.SUCCESS and result.recipe_version == 2
    assert all(record.evidence["name"].recipe_version == 2 for record in result.records)
    served = repo.get(key)
    assert served.recipe.version == 2 and served.metadata.state is RecipeState.ACTIVE
    assert repo.get(key, version=1).recipe == BAD_CSS_V1  # The rejected row is untouched.
    again = await engine.extract_html_async(html, source=source, schema=schema)
    assert again.recipe_reused and again.recipe_version == 2


@pytest.mark.asyncio
async def test_finding_2b_relearning_a_rejected_definition_activates_it(
    tmp_path, repo, key, html, source, schema
):
    scratch = SQLiteRecipeRepository(tmp_path / "scratch.db")
    await Rastro(repository=scratch, discover_recipes=True).extract_html_async(
        html, source=source, schema=schema
    )
    learned = scratch.get(key).recipe
    repo.save_candidate(key, learned, selected_index=0, at=NOW)
    repo.transition(key, 1, RecipeState.RETIRED, reason="Rejected by operator", at=NOW)
    engine = Rastro(repository=repo, discover_recipes=True)
    await engine.extract_html_async(html, source=source, schema=schema)
    assert repo.get(key).recipe.version == 2
    again = await engine.extract_html_async(html, source=source, schema=schema)
    assert again.recipe_reused  # No endless re-discovery.


def test_finding_3_deleting_a_version_deletes_its_operator_note(path, repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_candidate(key, recipe.model_copy(update={"version": 2}), selected_index=0, at=NOW)
    repo.transition(key, 2, RecipeState.ACTIVE, reason="V2_PRIVATE_OPERATOR_NOTE", at=NOW)
    sequences = [event.sequence for event in repo.history(key).events]
    assert repo.delete(key, version=2) == 1
    assert b"V2_PRIVATE_OPERATOR_NOTE" not in path.read_bytes()
    assert all("V2_PRIVATE" not in event.reason for event in repo.history(key).events)
    # Sequence numbers are not reissued while the key keeps a version.
    repo.rollback(key, 1, reason="Restore", at=NOW)
    assert repo.history(key).events[-1].sequence == max(sequences) + 1


def test_finding_3_delete_of_missing_version_writes_nothing(path, repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    before = repo.history(key)
    assert repo.delete(key, version=9) == 0
    assert repo.history(key) == before


def test_finding_4b_pinned_get_rejects_corrupt_validation(path, repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE recipes SET validated_at = NULL WHERE version = 1")
        db.commit()
    for read in (lambda: repo.get(key), lambda: repo.get(key, version=1)):
        with pytest.raises(RecipeStorageError):
            read()


@pytest.mark.parametrize("operation", ["history", "delete"])
def test_finding_4c_invalid_keys_fail_before_creating_files(path, repo, key, operation):
    invalid = RecipeKey.model_construct(**{**dict(key), "locale": "x"})
    with pytest.raises(ValidationError):
        getattr(repo, operation)(invalid)
    assert not path.exists()


def test_finding_5_candidates_live_only_on_the_lifecycle_port():
    assert "candidate" not in inspect.signature(RecipeRepository.save_success).parameters
    assert hasattr(RecipeLifecycle, "save_candidate")
    assert not hasattr(RecipeRepository, "save_candidate")


def test_finding_6_deleting_the_serving_version_leaves_no_serving_version(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_success(key, recipe.model_copy(update={"version": 2}), selected_index=0, at=NOW)
    assert repo.delete(key, version=2) == 1
    assert repo.get(key) is None
    assert repo.next_version(key) == 2
    repo.rollback(key, 1, reason="Restore after delete", at=NOW)
    assert repo.get(key).recipe.version == 1
