"""HUB-002, HUB-003, HUB-004, HUB-005, HUB-006: CLI integration for recipe hub phase 2."""

import json
import shutil
from pathlib import Path

import pytest

from rastro.cli import main

SAMPLE = Path(__file__).parents[2] / "examples/event-agenda/sample"
EXAMPLE_REGISTRY = Path(__file__).parents[2] / "examples/registry"

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.fixture
def author_job(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, Path]:
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
    capsys.readouterr()
    return folder, job


def test_format_1_bundle_still_imports_unchanged(
    author_job: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """HUB-002: export writes format 2 by default; a format-1 bundle still imports cleanly."""
    folder, job = author_job
    bundle = tmp_path / "bundle-v1"
    sample = folder / "teatro-central-cartelera.html"
    assert main(["recipe", "export", str(job), "--sample", str(sample), "--to", str(bundle)]) == 0
    capsys.readouterr()
    manifest_path = bundle / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert manifest["visibility"] == "private"
    v1_manifest = {
        "format_version": 1,
        "source": manifest["source"],
        "context": manifest["context"],
        "representation": manifest["representation"],
        "last_verified": manifest["last_verified"],
        "files": manifest["files"],
    }
    manifest_path.write_text(
        json.dumps(v1_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    store = tmp_path / "v1.sqlite"
    assert main(["recipe", "import", str(bundle), "--store", str(store)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "candidate"


def test_cli_export_publish_search_install_round_trip(
    author_job: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """HUB-002..HUB-006: full CLI flow from export to registry publish, search and install."""
    folder, job = author_job
    sample = folder / "teatro-central-cartelera.html"
    private_bundle = tmp_path / "private-bundle"
    assert (
        main(
            [
                "recipe",
                "export",
                str(job),
                "--sample",
                str(sample),
                "--to",
                str(private_bundle),
                "--id",
                "teatro-central-agenda",
                "--version",
                "1.0.0",
                "--author",
                "Rastro Team",
                "--license",
                "Apache-2.0",
            ]
        )
        == 0
    )
    capsys.readouterr()

    registry = tmp_path / "registry"
    assert main(["recipe", "publish", str(private_bundle), "--registry", str(registry)]) == 2
    err = json.loads(capsys.readouterr().err)
    assert "--public" in err["message"] or "--public" in err.get("hint", "")

    public_bundle = tmp_path / "public-bundle"
    assert (
        main(
            [
                "recipe",
                "export",
                str(job),
                "--sample",
                str(sample),
                "--to",
                str(public_bundle),
                "--id",
                "teatro-central-agenda",
                "--version",
                "1.0.0",
                "--author",
                "Rastro Team",
                "--license",
                "Apache-2.0",
                "--public",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["recipe", "publish", str(public_bundle), "--registry", str(registry)]) == 0
    pub_out = json.loads(capsys.readouterr().out)
    assert pub_out["id"] == "teatro-central-agenda"
    assert pub_out["version"] == "1.0.0"
    assert pub_out["trust"]["replay_verified"] is True
    assert any("git" in cmd for cmd in pub_out["git_commands"])

    assert main(["recipe", "search", "teatro", "--registry", str(registry)]) == 0
    search_out = json.loads(capsys.readouterr().out)
    assert len(search_out["results"]) == 1
    assert search_out["results"][0]["id"] == "teatro-central-agenda"
    assert search_out["results"][0]["trust"]["replay_verified"] is True

    store = tmp_path / "installed.sqlite"
    assert (
        main(
            [
                "recipe",
                "install",
                "teatro-central-agenda@1.0.0",
                "--registry",
                str(registry),
                "--store",
                str(store),
            ]
        )
        == 0
    )
    install_out = json.loads(capsys.readouterr().out)
    assert install_out["id"] == "teatro-central-agenda"
    assert install_out["package_version"] == "1.0.0"
    assert install_out["state"] == "candidate"
    assert install_out["trust"]["replay_verified"] is True
    assert install_out["published_trust"]["replay_verified"] is True


def test_example_registry_searches_and_installs_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """HUB-003, HUB-006: examples/registry contains a valid synthetic package."""
    assert main(["recipe", "search", "teatro", "--registry", str(EXAMPLE_REGISTRY)]) == 0
    found = json.loads(capsys.readouterr().out)
    assert len(found["results"]) >= 1
    pkg_id = found["results"][0]["id"]

    store = tmp_path / "example.sqlite"
    assert (
        main(
            [
                "recipe",
                "install",
                pkg_id,
                "--registry",
                str(EXAMPLE_REGISTRY),
                "--store",
                str(store),
            ]
        )
        == 0
    )
    installed = json.loads(capsys.readouterr().out)
    assert installed["id"] == pkg_id
    assert installed["trust"]["replay_verified"] is True
