"""PR #55 review fixes: read-only preview, separate stores, containment (DATA-001/002/005)."""

import json
import os
from pathlib import Path

import pytest

from rastro import JobExitCode, load_job, run_job_file
from rastro.cli import main
from rastro.domain.models import ConfigurationError

HEAD = "format_version = 1\n"
BODY = """
[recipe]
strategy = "css"
record_selector = "li"
fields = { title = "b::text" }

[[sources]]
path = "page.html"
url = "https://agenda.example.com/events"

[[fields]]
name = "title"
type = "string"
"""


def _job(tmp: Path, *, dataset: str = 'path = "agenda.sqlite"', extra: str = "", body: str = BODY):
    (tmp / "page.html").write_text("<ul><li><b>Tango</b></li></ul>")
    job = tmp / "job.toml"
    job.write_text(
        f'{HEAD}{extra}\n[dataset]\nname = "agenda"\n{dataset}\nidentity = ["title"]\n{body}'
    )
    return job


def _code(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().err


def test_preview_refuses_memory_enabled_jobs_and_writes_nothing(tmp_path, capsys):
    job = _job(tmp_path, extra='recipe_memory = "memory.sqlite"')
    code, err = _code(["job", "preview", str(job), "--output", "json"], capsys)
    assert code == JobExitCode.CONFIG_ERROR
    assert "recipe_memory" in json.loads(err)["message"]
    assert not (tmp_path / "memory.sqlite").exists()
    existing = tmp_path / "memory.sqlite"
    existing.write_bytes(b"not touched")
    assert main(["job", "preview", str(job)]) == JobExitCode.CONFIG_ERROR
    assert existing.read_bytes() == b"not touched"


def test_dataset_path_cannot_be_the_recipe_memory_file(tmp_path, capsys):
    job = _job(tmp_path, extra='recipe_memory = "agenda.sqlite"')
    for argv in (["job", "validate", str(job)], ["job", "preview", str(job)]):
        code, err = _code(argv, capsys)
        assert code == JobExitCode.CONFIG_ERROR and "separate" in json.loads(err)["message"]
    assert not (tmp_path / "agenda.sqlite").exists()


def test_dataset_symlink_and_hardlink_to_recipe_memory_are_rejected(tmp_path):
    (tmp_path / "memory.sqlite").write_bytes(b"")
    (tmp_path / "dataset.sqlite").symlink_to(tmp_path / "memory.sqlite")
    job = _job(tmp_path, dataset='path = "dataset.sqlite"', extra='recipe_memory = "memory.sqlite"')
    with pytest.raises(ConfigurationError, match="separate"):
        load_job(job)
    (tmp_path / "dataset.sqlite").unlink()
    os.link(tmp_path / "memory.sqlite", tmp_path / "dataset.sqlite")
    with pytest.raises(ConfigurationError, match="separate"):
        load_job(job)


def test_preview_missing_optional_field_is_not_observed_and_not_loaded(tmp_path, capsys):
    body = BODY + '\n[[fields]]\nname = "venue"\ntype = "string"\nrequired = false\n'
    job = _job(tmp_path, body=body)
    assert main(["job", "preview", str(job), "--output", "json"]) == JobExitCode.SUCCESS
    fields = json.loads(capsys.readouterr().out)["sources"][0]["records"][0]["fields"]
    venue = next(f for f in fields if f["name"] == "venue")
    assert venue["validation"] == "not_observed" and venue["loaded"] is False
    assert venue["method"] is None and venue["value"] is None
    title = next(f for f in fields if f["name"] == "title")
    assert title["validation"] == "valid" and title["loaded"] is True
    assert main(["job", "preview", str(job)]) == JobExitCode.SUCCESS
    assert "not_observed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "dataset", ['path = "/tmp/rastro-outside.sqlite"', 'path = "escape/o.sqlite"']
)
def test_dataset_containment_is_enforced_by_load_validate_run_and_preview(
    tmp_path, capsys, dataset
):
    root = tmp_path / "job"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (root / "escape").symlink_to(tmp_path / "outside")
    job = _job(root, dataset=dataset)
    with pytest.raises(ConfigurationError):
        load_job(job)
    with pytest.raises(ConfigurationError):
        run_job_file(job)
    for command in ("validate", "run", "preview"):
        code, err = _code(["job", command, str(job)], capsys)
        assert code == JobExitCode.CONFIG_ERROR, command
        assert json.loads(err)["error"] == "configuration_error"
    assert not any((tmp_path / "outside").iterdir())


def test_absolute_dataset_path_is_allowed_with_the_flag(tmp_path, capsys):
    target = tmp_path / "elsewhere" / "agenda.sqlite"
    job = _job(tmp_path, dataset=f'path = "{target}"')
    assert main(["job", "validate", str(job), "--allow-absolute-paths"]) == JobExitCode.SUCCESS
    assert not target.exists()
