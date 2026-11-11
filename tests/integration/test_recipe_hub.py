"""HUB-001: Git-friendly recipe bundles are verified offline before they are stored."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rastro.cli import main
from rastro.domain.lifecycle import RecipeState
from rastro.domain.recipe_bundle import MANIFEST_FILE, canonical_json, sha256_hex

SAMPLE = Path(__file__).parents[2] / "examples/uy-dance-agenda/sample"

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.fixture
def exported(tmp_path, capsys):
    folder = tmp_path / "author"
    shutil.copytree(SAMPLE, folder)
    job = folder / "job.toml"
    job.write_text(
        job.read_text(encoding="utf-8").replace(
            "format_version = 1", 'format_version = 1\nrecipe_memory = "recipes.sqlite"'
        ),
        encoding="utf-8",
    )
    assert main(["job", "load", str(job)]) == 0
    bundle = tmp_path / "bundle"
    args = ["recipe", "export", str(job), "--sample", str(folder / "sodre-cartelera.html")]
    assert main([*args, "--to", str(bundle)]) == 0
    capsys.readouterr()
    return folder, job, bundle


def rewrite_manifest(bundle: Path, **changes):
    manifest = json.loads((bundle / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest.update(changes)
    (bundle / MANIFEST_FILE).write_bytes(canonical_json(manifest))


def import_error(bundle, tmp_path, capsys):
    store = tmp_path / "b.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 2
    return capsys.readouterr().err, store


def test_export_is_deterministic_and_complete(exported, tmp_path):
    folder, job, bundle = exported
    names = {p.name for p in bundle.iterdir()}
    assert names == {"recipe.json", "schema.json", "sample.html", "expected.json", "bundle.json"}
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    again = tmp_path / "again"
    sample = folder / "sodre-cartelera.html"
    assert main(["recipe", "export", str(job), "--sample", str(sample), "--to", str(again)]) == 0
    after = {p.name: p.read_bytes() for p in again.iterdir()}
    assert after == before
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 1
    assert manifest["source"]["url"] == "https://sodre.gub.uy/espectaculos/"
    assert manifest["files"]["sample.html"] == sha256_hex(sample.read_bytes())
    assert b"\r" not in before["recipe.json"]


def test_git_roundtrip_serves_the_recipe_without_the_author(exported, tmp_path, capsys):
    """A third party clones the bundle and extracts the sample with only the bundle."""
    folder, _, bundle = exported
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "recipe"]):
        subprocess.run(["git", *command], cwd=bundle, env=env, check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bundle), str(clone)], check=True)
    store = tmp_path / "b.sqlite"
    assert main(["recipe", "import", str(clone), "--store", str(store)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "state": "candidate",
        "version": 1,
    }
    # Consumer job: its own file, its own empty store, the bundle's sample.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    shutil.copy(clone / "sample.html", consumer / "page.html")
    lines = (folder / "job.toml").read_text(encoding="utf-8").splitlines(keepends=True)
    top = "".join(lines[:17]).replace("recipes.sqlite", "b.sqlite")
    fields = "".join(lines[91:139])
    (consumer / "job.toml").write_text(
        top + '[[sources]]\nkind = "file"\npath = "page.html"\n'
        'url = "https://sodre.gub.uy/espectaculos/"\n\n' + fields,
        encoding="utf-8",
    )
    shutil.copy(store, consumer / "b.sqlite")
    assert (
        main(["recipe", "import", str(clone), "--store", str(consumer / "b.sqlite"), "--activate"])
        == 0
    )
    capsys.readouterr()
    assert main(["job", "load", str(consumer / "job.toml")]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["sources"][0]["status"] == "success"


def test_import_activate_serves_and_default_is_candidate(exported, tmp_path, capsys):
    _, _, bundle = exported
    store = tmp_path / "b.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store), "--activate"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == RecipeState.ACTIVE


@pytest.mark.parametrize("name", ["recipe.json", "expected.json", "sample.html", "schema.json"])
def test_tampered_file_fails_closed(exported, tmp_path, capsys, name):
    _, _, bundle = exported
    with (bundle / name).open("ab") as handle:
        handle.write(b" ")
    err, store = import_error(bundle, tmp_path, capsys)
    assert "digest" in err
    assert not store.exists()


def test_replay_mismatch_fails_even_with_valid_hashes(exported, tmp_path, capsys):
    _, _, bundle = exported
    expected = json.loads((bundle / "expected.json").read_text(encoding="utf-8"))
    expected[0]["title"] = "forged"
    data = canonical_json(expected)
    (bundle / "expected.json").write_bytes(data)
    manifest = json.loads((bundle / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["files"]["expected.json"] = sha256_hex(data)
    rewrite_manifest(bundle, files=manifest["files"])
    err, store = import_error(bundle, tmp_path, capsys)
    assert "differs" in err
    assert not store.exists()


def test_unknown_version_and_foreign_names_are_rejected(exported, tmp_path, capsys):
    _, _, bundle = exported
    rewrite_manifest(bundle, format_version=2)
    assert "format" in import_error(bundle, tmp_path, capsys)[0]
    rewrite_manifest(
        bundle,
        format_version=1,
        files={"../x": "0" * 64, "a": "0" * 64, "b": "0" * 64, "c": "0" * 64},
    )
    assert "unexpected" in import_error(bundle, tmp_path, capsys)[0]


def test_oversized_and_symlinked_files_are_rejected(exported, tmp_path, capsys):
    _, _, bundle = exported
    (bundle / "recipe.json").write_bytes(b" " * 1_048_577)
    assert "size" in import_error(bundle, tmp_path, capsys)[0]
    (bundle / "recipe.json").unlink()
    (bundle / "recipe.json").symlink_to(bundle / "expected.json")
    assert "linked" in import_error(bundle, tmp_path, capsys)[0]


def test_symlinked_bundle_directory_is_rejected(exported, tmp_path, capsys):
    _, _, bundle = exported
    link = tmp_path / "link"
    link.symlink_to(bundle, target_is_directory=True)
    assert "directory" in import_error(link, tmp_path, capsys)[0]


def test_export_requires_a_clean_sample(exported, tmp_path, capsys):
    job = exported[1]
    empty = tmp_path / "empty.html"
    empty.write_text("<html></html>", encoding="utf-8")
    target = tmp_path / "never"
    assert main(["recipe", "export", str(job), "--sample", str(empty), "--to", str(target)]) == 2
    assert "sample" in capsys.readouterr().err
    assert not target.exists()
