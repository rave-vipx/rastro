"""DATA-001 / REPAIR-002 / SCR-002: evidence retention never contradicts a committed load."""

import json

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main
from rastro.mocklab.errors import SanitizationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

JOB = """format_version = 1

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["link"]

[[fields]]
name = "title"
type = "string"

[[fields]]
name = "link"
type = "url"

[[sources]]
kind = "file"
path = "page.html"
url = "https://acme.example/careers"

[sources.recipe]
version = 1
strategy = "css"
record_selector = "li"

[sources.recipe.fields]
title = "h3::text"
link = "a::attr(href)"
"""

PAGE = (
    "<html><body><ul>"
    '<li><h3>Engineer</h3><a href="/jobs/101">Apply</a></li>'
    '<li><h3>Designer</h3><a href="https://acme.example/jobs/102">Apply</a></li>'
    "</ul></body></html>"
)


def _write(tmp_path):
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    job = tmp_path / "job.toml"
    job.write_text(JOB, encoding="utf-8")
    return job


def test_load_with_relative_urls_retains_a_replayable_fixture(tmp_path, capsys):
    job = _write(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["job"]["status"] == "success"
    assert "issues" not in output["sources"][0]
    links = sorted(
        r.fields["link"] for r in SQLiteDatasetStore(tmp_path / "jobs.sqlite").records("jobs")
    )
    assert links == ["https://acme.example/jobs/101", "https://acme.example/jobs/102"]
    # A second load replays and retains again without failing.
    assert main(["job", "load", str(job)]) == 0


def test_retention_failure_after_commit_is_a_reported_warning(tmp_path, capsys, monkeypatch):
    job = _write(tmp_path)

    def refuse(*args, **kwargs):
        raise SanitizationError("The source URL path looks like it contains a token")

    monkeypatch.setattr("rastro.cli_repair_storage.build_fixture", refuse)
    assert main(["job", "load", str(job)]) == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["job"]["status"] == "success"
    assert [i["code"] for i in output["sources"][0]["issues"]] == ["evidence_retention_failed"]
    assert len(SQLiteDatasetStore(tmp_path / "jobs.sqlite").records("jobs")) == 2
