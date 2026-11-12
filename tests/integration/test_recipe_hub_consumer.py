"""HUB-001, REC-006: a consumer adopts a shared bundle on a fresh recipe store."""

import json
import shutil
from pathlib import Path

import pytest

from rastro.cli import main
from tests.integration.test_recipe_hub import exported  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def _consumer(tmp_path: Path, folder: Path, bundle: Path) -> Path:
    """A job with recipe_memory and no explicit recipe, next to the bundle's sample."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    shutil.copy(bundle / "sample.html", consumer / "page.html")
    lines = (folder / "job.toml").read_text(encoding="utf-8").splitlines(keepends=True)
    job = consumer / "job.toml"
    job.write_text(
        "".join(lines[:17]) + '[[sources]]\nkind = "file"\npath = "page.html"\n'
        'url = "https://sodre.gub.uy/espectaculos/"\n\n' + "".join(lines[91:139]),
        encoding="utf-8",
    )
    return job


def test_export_import_promote_load_on_a_fresh_store(exported, tmp_path, capsys):  # noqa: F811
    """HUB-001, REC-006: an imported candidate is promotable on a store with no serving
    version, and a consumer job without an explicit recipe then loads it."""
    folder, _, bundle = exported
    job = _consumer(tmp_path, folder, bundle)
    store = job.parent / "recipes.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 0
    capsys.readouterr()
    promote = ["recipe", "promote", str(job), "--source", "1", "--version", "1"]
    assert main([*promote, "--reason", "Reviewed shared bundle"]) == 0
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["sources"][0]["comparisons"][0]["evaluation"]["decision"] == "promote"
    assert main(["job", "load", str(job)]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["sources"][0]["status"] == "success"


def test_reimporting_the_same_bundle_reuses_the_stored_version(
    exported,  # noqa: F811
    tmp_path,
    capsys,
):
    """HUB-001: an identical recipe definition for the same key is not stored twice."""
    _, _, bundle = exported
    store = tmp_path / "b.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["reused"] is False
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == {**first, "reused": True}


def test_adoption_still_runs_the_gates_on_the_consumer_page(exported, tmp_path, capsys):  # noqa: F811
    """REC-006: with nothing serving, a candidate that fails on the consumer's page stays
    a candidate and the job still has no recipe to load."""
    folder, _, bundle = exported
    job = _consumer(tmp_path, folder, bundle)
    (job.parent / "page.html").write_text("<html><body></body></html>", encoding="utf-8")
    store = job.parent / "recipes.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 0
    promote = ["recipe", "promote", str(job), "--source", "1", "--version", "1"]
    assert main([*promote, "--reason", "Reviewed shared bundle"]) != 0
    capsys.readouterr()
    assert main(["job", "load", str(job)]) != 0


def test_reimport_after_retirement_stores_a_new_candidate(exported, tmp_path):  # noqa: F811
    """HUB-001, REC-005: an equal but retired version is not reused by import."""
    from datetime import UTC, datetime

    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.application.recipe_hub import import_bundle
    from rastro.cli_hub import _parser_for
    from rastro.domain.lifecycle import RecipeState

    _, _, bundle = exported
    repository = SQLiteRecipeRepository(tmp_path / "b.sqlite")
    now = datetime.now(UTC)
    first = import_bundle(bundle, repository, _parser_for, at=now)
    assert import_bundle(bundle, repository, _parser_for, at=now).reused is True
    key = first.stored.key
    repository.transition(key, 1, RecipeState.RETIRED, reason="Rejected", at=now)
    again = import_bundle(bundle, repository, _parser_for, at=now)
    assert again.reused is False
    assert (again.stored.recipe.version, again.stored.metadata.state) == (2, "candidate")
