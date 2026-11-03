import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rastro import ConfigurationError, Outcome, Rastro
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_lifecycle import SUPERSEDED_REASON
from rastro.domain.lifecycle import LifecycleAction, LifecycleTransitionError, RecipeState
from rastro.domain.lifecycle_history import LifecycleHistory
from rastro.domain.memory import RecipeKey, RecipeNotFoundError, RecipeStorageError

NOW = datetime(2026, 1, 2, tzinfo=UTC)
C, A, D, R = RecipeState.CANDIDATE, RecipeState.ACTIVE, RecipeState.DEGRADED, RecipeState.RETIRED


@pytest.fixture
def repo(tmp_path) -> SQLiteRecipeRepository:
    return SQLiteRecipeRepository(tmp_path / "recipes.db")


@pytest.fixture
def key(source, schema) -> RecipeKey:
    return RecipeKey.for_source(source, schema)


def states(repo, key) -> dict[int, RecipeState]:
    return {item.version: item.state for item in repo.history(key).versions}


def actions(repo, key) -> list[tuple[int, LifecycleAction]]:
    return [(event.version, event.action) for event in repo.history(key).events]


def v(recipe, number):
    return recipe.model_copy(update={"version": number})


def test_new_versions_become_active_and_supersede_the_serving_one(repo, key, recipe):
    first = repo.save_success(key, recipe, selected_index=0, at=NOW)
    assert first.metadata.state is A
    later = NOW + timedelta(hours=1)
    assert repo.save_success(key, v(recipe, 2), selected_index=0, at=later).metadata.state is A
    # Repeated success on an existing version updates history, never state.
    assert repo.save_success(key, recipe, selected_index=0, at=later).metadata.state is R
    assert repo.get(key).recipe.version == 2
    assert repo.get(key, version=1).metadata.state is R
    history = repo.history(key)
    assert [(e.version, e.from_state, e.to_state, e.action) for e in history.events] == [
        (1, None, A, LifecycleAction.CREATED),
        (1, A, R, LifecycleAction.SUPERSEDED),
        (2, None, A, LifecycleAction.CREATED),
    ]
    assert [e.sequence for e in history.events] == [1, 2, 3]
    assert history.events[1].at == later
    assert {item.version: item.validated_at for item in history.versions} == {1: NOW, 2: later}
    assert history.serving.version == 2


def test_candidate_never_replaces_the_active_recipe(repo, key, recipe, source, schema, html):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    candidate = repo.save_candidate(key, v(recipe, 2), selected_index=0, at=NOW)
    assert candidate.metadata.state is C
    assert states(repo, key) == {1: A, 2: C}
    result = Rastro(repository=repo).extract_html(html, source=source, schema=schema)
    assert result.status == Outcome.SUCCESS and result.recipe_reused
    assert result.recipe_version == 1
    # A failed candidate is rejected; the known working recipe keeps serving (REC-006).
    rejected = repo.transition(key, 2, R, reason="Failed regression fixtures", at=NOW)
    assert rejected.metadata.state is R
    assert repo.get(key).recipe.version == 1
    with pytest.raises(LifecycleTransitionError, match="previously validated"):
        repo.rollback(key, 2, reason="Try the rejected candidate", at=NOW)
    assert states(repo, key) == {1: A, 2: R}
    assert actions(repo, key)[-1] == (2, LifecycleAction.REJECTED)


def test_late_lower_version_success_does_not_replace_a_newer_one(repo, key, recipe):
    repo.save_success(key, v(recipe, 3), selected_index=0, at=NOW)
    late = repo.save_success(key, v(recipe, 2), selected_index=0, at=NOW + timedelta(seconds=1))
    assert late.metadata.state is C
    assert repo.get(key).recipe.version == 3


def test_candidate_without_serving_version_is_not_reused(repo, key, recipe):
    repo.save_candidate(key, recipe, selected_index=0, at=NOW)
    assert repo.get(key) is None
    assert repo.get(key, version=1).metadata.state is C
    assert repo.history(key).serving is None


def test_promotion_and_rollback_record_reason_and_timestamp(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_candidate(key, v(recipe, 2), selected_index=0, at=NOW)
    promoted_at, rolled_at = NOW + timedelta(days=1), NOW + timedelta(days=2)
    repo.transition(key, 2, A, reason="Passed all thresholds", at=promoted_at)
    assert states(repo, key) == {1: R, 2: A}
    restored = repo.rollback(key, 1, reason="Regression reported by operator", at=rolled_at)
    assert restored.metadata.state is A and repo.get(key).recipe.version == 1
    assert states(repo, key) == {1: A, 2: R}
    events = repo.history(key).events[-4:]
    assert [(e.version, e.action, e.reason, e.at) for e in events] == [
        (1, LifecycleAction.SUPERSEDED, SUPERSEDED_REASON, promoted_at),
        (2, LifecycleAction.PROMOTED, "Passed all thresholds", promoted_at),
        (2, LifecycleAction.SUPERSEDED, SUPERSEDED_REASON, rolled_at),
        (1, LifecycleAction.ROLLED_BACK, "Regression reported by operator", rolled_at),
    ]
    # Version 2 served once, so it is a valid rollback target; validation time is kept.
    repo.rollback(key, 2, reason="Undo rollback", at=rolled_at + timedelta(hours=1))
    validated = {item.version: item.validated_at for item in repo.history(key).versions}
    assert validated == {1: NOW, 2: promoted_at}


def test_degraded_version_keeps_serving_until_recovered_or_retired(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.transition(key, 1, D, reason="Drift detected", at=NOW)
    assert repo.get(key).metadata.state is D
    repo.transition(key, 1, A, reason="Source recovered", at=NOW)
    repo.transition(key, 1, R, reason="Source retired", at=NOW)
    assert repo.get(key) is None
    assert [a for _, a in actions(repo, key)] == [
        LifecycleAction.CREATED,
        LifecycleAction.DEGRADED,
        LifecycleAction.RECOVERED,
        LifecycleAction.RETIRED,
    ]
    repo.rollback(key, 1, reason="Retired by mistake", at=NOW)
    assert repo.get(key).metadata.state is A


def test_forbidden_transitions_change_nothing(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.transition(key, 1, R, reason="Retire", at=NOW)
    before = repo.history(key)
    for target in (A, D, C, R):
        with pytest.raises(LifecycleTransitionError):
            repo.transition(key, 1, target, reason="Not allowed", at=NOW)
    assert repo.history(key) == before


@pytest.mark.parametrize(
    ("version", "state", "reason", "error"),
    [
        (9, R, "Missing", RecipeNotFoundError),
        (0, R, "Out of range", ConfigurationError),
        (None, R, "Required", ConfigurationError),
        (1, "archived", "Unknown state", ConfigurationError),
        (1, R, "", ConfigurationError),
        (1, R, "x" * 501, ConfigurationError),
        (1, R, "line\nbreak", ConfigurationError),
    ],
)
def test_invalid_lifecycle_requests_fail_explicitly(
    repo, key, recipe, version, state, reason, error
):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    with pytest.raises(error):
        repo.transition(key, version, state, reason=reason, at=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.rollback(key, 1, reason="Naive", at=NOW.replace(tzinfo=None))
    assert states(repo, key) == {1: A}


def test_history_is_machine_readable_and_round_trips(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_candidate(key, v(recipe, 2), selected_index=0, at=NOW)
    history = repo.history(key)
    payload = json.loads(history.model_dump_json())
    assert payload["versions"] == [
        {"version": 1, "state": "active", "validated_at": "2026-01-02T00:00:00Z"},
        {"version": 2, "state": "candidate", "validated_at": None},
    ]
    assert payload["events"][-1]["action"] == "created"
    assert payload["events"][-1]["reason"] == "Stored candidate"
    assert LifecycleHistory.model_validate_json(history.model_dump_json()) == history
    assert repo.history(key.model_copy(update={"locale": "fr-FR"})).events == ()


def test_delete_and_prune_remove_lifecycle_history(repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_success(key, v(recipe, 2), selected_index=0, at=NOW + timedelta(days=2))
    assert repo.prune(before=NOW + timedelta(days=1)) == 1
    assert {e.version for e in repo.history(key).events} == {2}
    assert repo.delete(key) == 1
    assert repo.history(key) == LifecycleHistory(key=key, versions=(), events=())
    # Sequences restart only once a key has no remaining history.
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    assert [e.sequence for e in repo.history(key).events] == [1]


def test_storage_enforces_one_serving_version(tmp_path, repo, key, recipe):
    repo.save_success(key, recipe, selected_index=0, at=NOW)
    repo.save_success(key, v(recipe, 2), selected_index=0, at=NOW)
    with closing(sqlite3.connect(tmp_path / "recipes.db")) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE recipes SET state = 'active'")
        db.execute("UPDATE recipes SET validated_at = NULL WHERE version = 2")
        db.commit()
    with pytest.raises(RecipeStorageError):
        repo.history(key)
