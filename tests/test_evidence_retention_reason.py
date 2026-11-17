"""N17 / REPAIR-002: a refused evidence sample names its field and category, never the value."""

import json
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel

import rastro
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

PHONE = "+598 99 123 456"
JOB = """format_version = 1

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["title"]

[[fields]]
name = "title"
type = "string"

[[fields]]
name = "descripcion"
type = "string"

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
descripcion = "p::text"
"""
PAGE = f"<ul><li><h3>Engineer</h3><p>Call {PHONE}</p></li></ul>"


def test_job_load_reports_field_and_category_without_the_value(tmp_path, capsys):
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    job = tmp_path / "job.toml"
    job.write_text(JOB, encoding="utf-8")
    assert main(["job", "load", str(job)]) == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["job"]["status"] == "success"
    [issue] = result["sources"][0]["issues"]
    assert issue["code"] == "evidence_retention_failed"
    assert issue["field"] == "descripcion"
    assert issue["message"] == "sensitive_data:phone"
    assert PHONE not in out and "123 456" not in out


def test_scrape_memory_reports_field_and_category(tmp_path):
    class Offer(BaseModel):
        __records__: ClassVar[str] = "li"
        title: Annotated[str, rastro.css("h3::text")]
        descripcion: Annotated[str, rastro.css("p::text")]

    result = rastro.scrape(
        PAGE, Offer, url="https://acme.example/careers", memory=tmp_path / "recipes.db"
    )
    [issue] = [i for i in result.issues if i.code == "evidence_retention_failed"]
    assert (issue.field, issue.message) == ("descripcion", "sensitive_data:phone")
    assert PHONE not in json.dumps([i.model_dump() for i in result.issues])
