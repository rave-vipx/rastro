"""REC-005: `rastro recipe prune` never deletes a serving version."""

import json
from datetime import UTC, datetime, timedelta

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.cli import main
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey

OLD = datetime.now(UTC) - timedelta(days=100)


def _store(tmp_path, source, schema, recipe):
    path = tmp_path / "recipes.db"
    repo = SQLiteRecipeRepository(path)
    key = RecipeKey.for_source(source, schema)
    repo.save_success(key, recipe, selected_index=0, at=OLD)
    repo.save_success(key, recipe.model_copy(update={"version": 2}), selected_index=0, at=OLD)
    return path, repo, key


def test_prune_spares_the_serving_version_and_dry_run_counts(
    tmp_path, capsys, source, schema, recipe
):
    path, repo, key = _store(tmp_path, source, schema, recipe)
    args = ["recipe", "prune", "--store", str(path), "--older-than", "30"]
    assert main([*args, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "dry_run": True,
        "would_prune": 1,
    }
    assert len(repo.history(key).versions) == 2
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["pruned"] == 1
    remaining = repo.history(key).versions
    assert [(v.version, v.state) for v in remaining] == [(2, RecipeState.ACTIVE)]
    assert repo.get(key) is not None


def test_prune_requires_an_existing_store(tmp_path):
    missing = tmp_path / "none.db"
    assert main(["recipe", "prune", "--store", str(missing), "--older-than", "1"]) == 2
    assert not missing.exists()


def test_prune_keeps_the_newest_version_so_numbers_are_never_reused(
    tmp_path, source, schema, recipe
):
    """REC-005: even without keep_serving, the newest version of a key survives, so the
    next saved recipe cannot take over a pruned version number."""
    _, repo, key = _store(tmp_path, source, schema, recipe)
    assert repo.prune(before=datetime.now(UTC)) == 1
    assert [v.version for v in repo.history(key).versions] == [2]
