"""One-step hub publish: a job's serving recipe becomes a reviewed package in a registry checkout.

Publishing writes the bundle, the operator's synthetic sample, field descriptions and the
recipe's engine needs into a local directory ready for a pull request. Nothing goes over the
network and nothing is written when a gate fails.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.hub_publish import PackageInfo, publish_job_to_hub
from rastro.application.hub_search import hub_candidates, install_candidate
from rastro.cli import main
from rastro.cli_hub import _parser_for
from rastro.cli_hub_group import add_command, hub_group_command
from rastro.domain.models import ConfigurationError
from rastro.domain.registry import RegistryError

SAMPLE = Path(__file__).parents[2] / "examples/event-agenda/sample"
FIELDS = (
    "title",
    "performers",
    "venue",
    "city",
    "starts_at",
    "ticket_url",
    "price",
    "currency",
    "performance_id",
)
PACKAGE = PackageInfo(
    id="agenda-example", version="1.0.0", author="Rastro Contributors", license="Apache-2.0"
)
TODAY = date(2026, 11, 19)

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


def _descriptions() -> dict[str, str]:
    return {name: f"The event's {name.replace('_', ' ')}" for name in FIELDS}


def _publish(job: Path, folder: Path, registry: Path, **overrides: object):
    options: dict[str, object] = {
        "source_index": 1,
        "sample": folder / "teatro-central-cartelera.html",
        "package": PACKAGE,
        "descriptions": _descriptions(),
        "today": TODAY,
        **overrides,
    }
    return publish_job_to_hub(job, registry, _parser_for, **options)  # type: ignore[arg-type]


def test_publish_one_step_creates_bundle(author_job: tuple[Path, Path], tmp_path: Path) -> None:
    folder, job = author_job
    registry = tmp_path / "hub-checkout"
    published = _publish(job, folder, registry)
    package = registry / "recipes/agenda-example/1.0.0"
    names = {path.name for path in package.iterdir()}
    assert {"bundle.json", "recipe.json", "schema.json", "expected.json", "sample.html"} <= names
    assert "card.json" in names
    index = json.loads((registry / "index.json").read_text())
    assert [(item["id"], item["version"]) for item in index] == [("agenda-example", "1.0.0")]
    card = json.loads((package / "card.json").read_text())
    assert [item["name"] for item in card["fields"]] == list(FIELDS)
    assert card["engine_needs"] == {"capabilities": [], "render": "http", "representation": "html"}
    assert published.card.engine_needs.render == "http"
    assert any("git" in command for command in published.published.git_commands)
    [offer] = hub_candidates("https://agenda.example/otra-pagina", registry=registry)
    assert offer.card == published.card
    repository = SQLiteRecipeRepository(tmp_path / "consumer.sqlite")
    installed = install_candidate(offer, repository, _parser_for, at=datetime.now(UTC))
    assert installed.imported.stored.metadata.state == "candidate"


def test_publish_needs_a_description_for_every_field(
    author_job: tuple[Path, Path], tmp_path: Path
) -> None:
    folder, job = author_job
    registry = tmp_path / "hub-checkout"
    missing = {k: v for k, v in _descriptions().items() if k != "venue"}
    with pytest.raises(ConfigurationError, match="venue"):
        _publish(job, folder, registry, descriptions=missing)
    unknown = {**_descriptions(), "nonexistent": "Not a field"}
    with pytest.raises(ConfigurationError, match="nonexistent"):
        _publish(job, folder, registry, descriptions=unknown)
    bad = {**_descriptions(), "title": "line\nbreak"}
    with pytest.raises(ConfigurationError):
        _publish(job, folder, registry, descriptions=bad)
    assert not registry.exists()


def test_publish_refuses_secrets_in_descriptions_and_writes_nothing(
    author_job: tuple[Path, Path], tmp_path: Path
) -> None:
    folder, job = author_job
    registry = tmp_path / "hub-checkout"
    leaked = {**_descriptions(), "title": "Contact ana.perez@mail.example for access"}
    with pytest.raises(RegistryError, match=r"card\.json") as error:
        _publish(job, folder, registry, descriptions=leaked)
    assert "ana.perez" not in str(error.value)
    assert not (registry / "recipes").exists() or not any((registry / "recipes").iterdir())
    assert not (registry / "index.json").exists()


def test_publish_never_overwrites_a_published_version(
    author_job: tuple[Path, Path], tmp_path: Path
) -> None:
    folder, job = author_job
    registry = tmp_path / "hub-checkout"
    _publish(job, folder, registry)
    before = (registry / "recipes/agenda-example/1.0.0/card.json").read_bytes()
    with pytest.raises(RegistryError, match="already exists"):
        _publish(job, folder, registry, descriptions={**_descriptions(), "title": "Changed"})
    assert (registry / "recipes/agenda-example/1.0.0/card.json").read_bytes() == before


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    add_command(parser.add_subparsers(dest="command", required=True))
    return hub_group_command(parser.parse_args(argv))


def test_cli_hub_publish_and_search(
    author_job: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, job = author_job
    registry = tmp_path / "hub-checkout"
    describe = [
        arg for name, text in _descriptions().items() for arg in ("--describe", f"{name}={text}")
    ]
    argv = [
        "hub",
        "publish",
        str(job),
        "--sample",
        str(folder / "teatro-central-cartelera.html"),
        "--registry",
        str(registry),
        "--id",
        "agenda-example",
        "--version",
        "1.0.0",
        "--author",
        "Rastro Contributors",
        "--license",
        "Apache-2.0",
        *describe,
    ]
    assert _cli(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["id"], out["version"]) == ("agenda-example", "1.0.0")
    assert out["engine_needs"]["render"] == "http"
    assert any("git" in command for command in out["git_commands"])
    assert _cli(["hub", "search", "https://agenda.example/x", "--registry", str(registry)]) == 0
    found = json.loads(capsys.readouterr().out)
    assert [(r["id"], r["state"]) for r in found["candidates"]] == [("agenda-example", "candidate")]
    with pytest.raises(ConfigurationError):
        _cli([*argv[:-2], "--describe", "no-equals-sign"])
