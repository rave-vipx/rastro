"""`rastro suggest` and `rastro new` (INT-002, JOB-001, DISC-003)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from rastro.cli import main
from rastro.cli_suggest import parse_examples

PAGE = """<html><body><div class="jobs">
 <article class="card"><h2>Senior Dev</h2><span class="salary">120</span>
   <a href="/jobs/1">More</a><time datetime="2026-03-01">March</time></article>
 <article class="card"><h2>Analyst&#x1b;[31m</h2><span class="salary">95</span>
   <a href="/jobs/2">More</a><time datetime="2026-04-01">April</time></article>
</div></body></html>"""
URL = "https://acme.example/careers"
EXAMPLES = [
    "--example",
    "title=Senior Dev",
    "--example",
    "salary=120",
    "--example",
    "link=https://acme.example/jobs/1",
    "--example",
    "posted=2026-03-01",
]


@pytest.fixture
def page(tmp_path: Path) -> Path:
    path = tmp_path / "page.html"
    path.write_text(PAGE, encoding="utf-8")
    return path


def test_parse_examples_starts_a_record_when_a_field_repeats():
    assert parse_examples(["a=1", "b=x=y", "a=2"]) == [{"a": "1", "b": "x=y"}, {"a": "2"}]


def test_suggest_prints_recipe_json(page, capsys):
    assert main(["suggest", str(page), "--url", URL, *EXAMPLES]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recipe"]["record_selector"] == "article.card"
    assert payload["recipe"]["fields"]["link"] == "a::attr(href)"
    assert payload["status"] == "success"
    assert payload["records"] == 2


def test_suggest_table_never_prints_raw_control_characters(page, capsys):
    assert main(["suggest", str(page), "--output", "table", *EXAMPLES[:4]]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    # libxml2 builds differ: the control character is either escaped or dropped by the parser.
    assert "Analyst\\x1b[31m | 95" in out or "Analyst[31m | 95" in out


def test_suggest_failure_is_one_json_error(page, capsys):
    assert main(["suggest", str(page), "--example", "title=Nowhere"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "configuration_error"
    assert "example 1 'title'" in error["message"]


def test_new_writes_a_typed_job_that_validates_and_runs(page, tmp_path, capsys):
    job = tmp_path / "job.toml"
    args = ["new", str(job), "--from", str(page), "--url", URL, *EXAMPLES]
    assert main([*args, "--dataset", "offers.sqlite", "--identity", "title"]) == 0
    capsys.readouterr()
    text = job.read_text(encoding="utf-8")
    assert "rastro job run" in text and "no writes" in text and "rastro job load" in text
    data = tomllib.loads(text)
    types = {item["name"]: item["type"] for item in data["fields"]}
    assert types == {"title": "string", "salary": "integer", "link": "url", "posted": "date"}
    assert data["sources"] == [{"kind": "file", "path": "page.html", "url": URL}]
    assert data["dataset"] == {"name": "offers", "path": "offers.sqlite", "identity": ["title"]}
    assert main(["job", "validate", str(job)]) == 0
    capsys.readouterr()
    assert main(["job", "run", str(job)]) == 0
    report = json.loads(capsys.readouterr().out)
    records = report["results"][0]["records"]
    assert records[1]["data"]["link"] == "https://acme.example/jobs/2"


def test_new_keeps_string_when_page_values_do_not_fit_the_inferred_type(tmp_path, capsys):
    page = tmp_path / "page.html"
    page.write_text(
        "<li class='o'><b>A</b><i>12</i></li><li class='o'><b>B</b><i>n/a</i></li>",
        encoding="utf-8",
    )
    job = tmp_path / "job.toml"
    assert (
        main(["new", str(job), "--from", str(page), "--example", "name=A", "--example", "n=12"])
        == 0
    )
    types = {item["name"]: item["type"] for item in tomllib.loads(job.read_text())["fields"]}
    assert types["n"] == "string"


def test_new_can_enable_recipe_memory(page, tmp_path, capsys):
    """REC-006, JOB-001: --recipe-memory writes a top-level recipe_memory the job can repair."""
    job = tmp_path / "job.toml"
    args = ["new", str(job), "--from", str(page), "--url", URL, *EXAMPLES]
    args += ["--recipe-memory", "recipes.sqlite"]
    assert main(args) == 0
    assert tomllib.loads(job.read_text(encoding="utf-8"))["recipe_memory"] == "recipes.sqlite"


@pytest.mark.parametrize("memory", ["missing/recipes.sqlite", "../recipes.sqlite", "/tmp/r.db"])
def test_new_refuses_a_bad_recipe_memory_path(page, tmp_path, capsys, memory):
    job = tmp_path / "job.toml"
    args = ["new", str(job), "--from", str(page), "--url", URL, *EXAMPLES]
    args += ["--recipe-memory", memory]
    assert main(args) == 2
    assert "recipe_memory" in json.loads(capsys.readouterr().err)["message"]
    assert not job.exists()


# (dataset, recipe_memory) pairs that name the job file, the page or each other (JOB-001).
CLASHES = [
    (None, "job.toml"),
    (None, "./job.toml"),
    (None, "page.html"),
    ("data.sqlite", "./data.sqlite"),
    ("data.sqlite", "data.sqlite"),
    ("job.toml", None),
    ("./page.html", None),
]


@pytest.mark.parametrize(("dataset", "memory"), CLASHES)
def test_new_refuses_paths_that_name_the_same_file(page, tmp_path, capsys, dataset, memory):
    """JOB-001, REC-006: paths are compared after resolving them against the job folder."""
    job = tmp_path / "job.toml"
    args = ["new", str(job), "--from", str(page), "--url", URL, *EXAMPLES]
    if dataset is not None:
        args += ["--dataset", dataset, "--identity", "title"]
    if memory is not None:
        args += ["--recipe-memory", memory]
    assert main(args) == 2
    assert "same file" in json.loads(capsys.readouterr().err)["message"]
    assert not job.exists()
    assert page.read_text(encoding="utf-8") == PAGE


def test_new_refuses_to_overwrite(page, tmp_path, capsys):
    job = tmp_path / "job.toml"
    job.write_text("keep", encoding="utf-8")
    assert main(["new", str(job), "--from", str(page), *EXAMPLES]) == 2
    assert "refusing to overwrite" in json.loads(capsys.readouterr().err)["message"]
    assert job.read_text(encoding="utf-8") == "keep"


def test_new_refuses_a_page_outside_the_job_directory(page, tmp_path, capsys):
    (tmp_path / "jobs").mkdir()
    job = tmp_path / "jobs" / "job.toml"
    assert main(["new", str(job), "--from", str(page), *EXAMPLES]) == 2
    assert "inside the new job file's directory" in capsys.readouterr().err
    assert not job.exists()


def test_new_requires_identity_with_dataset(page, tmp_path, capsys):
    job = tmp_path / "job.toml"
    assert main(["new", str(job), "--from", str(page), *EXAMPLES, "--dataset", "d.sqlite"]) == 2
    assert "--identity" in capsys.readouterr().err
    assert not job.exists()


def test_new_rejects_unsafe_field_names(page, tmp_path, capsys):
    job = tmp_path / "job.toml"
    assert main(["new", str(job), "--from", str(page), "--example", "bad name=x"]) == 2
    assert not job.exists()


def test_new_refuses_a_partial_preview_unless_allowed(tmp_path, capsys):
    """JOB-001: `rastro new` writes a job only when its recipe preview fully succeeds."""
    page = tmp_path / "page.html"
    page.write_text(
        "<li class='o'><b>A</b><i>12</i></li><li class='o'><b>B</b><i>7</i></li>"
        "<li class='o'><b>C</b></li>",
        encoding="utf-8",
    )
    job = tmp_path / "job.toml"
    command = ["new", str(job), "--from", str(page), "--example", "name=A", "--example", "n=12"]
    assert main(command) == 2
    error = capsys.readouterr().err
    assert "partial_success" in error and "--allow-partial" in error
    assert not job.exists()
    assert main([*command, "--allow-partial"]) == 0
    assert job.exists()


def test_suggestion_status_reports_the_preview_outcome():
    """INT-002: suggest returns a partial preview with an explicit status."""
    from rastro import suggest
    from rastro.domain.job_fields import StringField

    html = (
        "<li class='o'><b>A</b><i>12</i></li><li class='o'><b>B</b><i>7</i></li>"
        "<li class='o'><b>C</b></li>"
    )
    found = suggest(
        html,
        [StringField(name="name"), StringField(name="n")],
        examples=[{"name": "A", "n": "12"}],
    )
    assert found.status == "partial_success"
