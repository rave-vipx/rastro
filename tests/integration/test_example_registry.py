"""N6 / HUB-002..006: synthetic examples replay, install as candidates and deduplicate."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.recipe_registry import install_from_registry, search_registry
from rastro.domain.lifecycle import RecipeState

REGISTRY = Path(__file__).resolve().parents[2] / "examples" / "registry"


@pytest.mark.parametrize("kind", ["listing", "news", "product"])
def test_example_install_replays_and_reuses_candidate(tmp_path: Path, kind: str) -> None:
    from examples.registry.build import parser_for

    entries = search_registry(REGISTRY, f"synthetic-{kind}")
    assert len(entries) == 1
    assert entries[0].license == "Apache-2.0"
    assert search_registry(REGISTRY, f"{kind}.example") == entries
    repo = SQLiteRecipeRepository(tmp_path / "recipes.sqlite")
    first = install_from_registry(
        REGISTRY, entries[0].id, repo, parser_for, at=datetime(2026, 11, 16, tzinfo=UTC)
    )
    assert first.trust.replay_verified
    assert not first.imported.reused
    key = first.imported.stored.key
    assert repo.get(key) is None
    assert repo.history(key).versions[0].state == RecipeState.CANDIDATE
    again = install_from_registry(
        REGISTRY,
        f"{entries[0].id}@1.0.0",
        repo,
        parser_for,
        at=datetime(2026, 11, 16, tzinfo=UTC),
    )
    assert again.imported.reused
    assert again.imported.stored.recipe.version == first.imported.stored.recipe.version
    assert len(repo.history(key).versions) == 1


def test_example_registry_build_is_reproducible(tmp_path: Path) -> None:
    """N6: the export/publish services reproduce every committed package byte offline."""
    from examples.registry.build import build

    generated = tmp_path / "registry"
    build(generated)
    for item in (REGISTRY / "recipes").rglob("*"):
        if item.is_file():
            assert (generated / item.relative_to(REGISTRY)).read_bytes() == item.read_bytes()
    assert (generated / "index.json").read_bytes() == (REGISTRY / "index.json").read_bytes()
    assert len(json.loads((generated / "index.json").read_text())) == 4
    for kind in ("listing", "news", "product"):
        assert (generated / f"{kind}.json").read_bytes() == (REGISTRY / f"{kind}.json").read_bytes()


@pytest.mark.usefixtures("offline_cli_loop")
@pytest.mark.parametrize("kind", ["listing", "news", "product"])
def test_example_cli_promote_load_and_rerun(
    tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """N6/N8: the documented candidate-to-dataset workflow survives a second load."""
    import shutil

    from rastro.cli import main

    registry = tmp_path / "registry"
    shutil.copytree(REGISTRY, registry)
    store = registry / f"{kind}-recipes.sqlite"
    job = str(registry / f"{kind}.json")
    install = ["recipe", "install", f"synthetic-{kind}@1.0.0", "--registry", str(registry)]
    assert main([*install, "--store", str(store)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "candidate"
    promote = ["recipe", "promote", job, "--source", "1", "--version", "1"]
    assert main([*promote, "--reason", "Reviewed synthetic replay"]) == 0
    capsys.readouterr()
    assert main(["job", "load", job]) == 0
    assert json.loads(capsys.readouterr().out)["sources"][0]["counts"]["created"] == 2
    assert main(["job", "load", job]) == 0
    counts = json.loads(capsys.readouterr().out)["sources"][0]["counts"]
    assert counts["created"] == 0
    assert counts["unchanged"] == 2
