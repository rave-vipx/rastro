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
    assert "Analyst\\x1b[31m | 95" in out


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
    data = tomllib.loads(job.read_text(encoding="utf-8"))
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
