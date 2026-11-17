"""Per-source rejections and reached caps in the engine's run metrics, seen by Studio (N5,
OBS-003..007, SYNC-002). Invalid or capped runs never read as a clean success."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rastro.adapters.dns import SystemResolver
from rastro.adapters.http import CurlTransport
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.job_stats import job_stats
from rastro.cli import main
from rastro.domain.acquisition import HttpResponse

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.studio.app import create_app
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
BASE = "http://127.0.0.1:8765"
URL = "https://shop.example/list"

FILE_JOB = """format_version = 1
[dataset]
name = "items"
path = "data.sqlite"
identity = ["sku"]
[[fields]]
name = "sku"
type = "string"
[[fields]]
name = "price"
type = "integer"
[[sources]]
kind = "file"
path = "page.html"
url = "https://shop.example/list"
[sources.recipe]
strategy = "css"
record_selector = ".item"
[sources.recipe.fields]
sku = "::attr(data-sku)"
price = ".price::text"
"""
# Two valid records, one with a price that is not a number, one without its required sku.
PAGE = (
    '<div class="item" data-sku="A"><b class="price">10</b></div>'
    '<div class="item" data-sku="B"><b class="price">12</b></div>'
    '<div class="item" data-sku="C"><b class="price">cheap</b></div>'
    '<div class="item"><b class="price">9</b></div>'
)
PAGED_JOB = """format_version = 1
[[fields]]
name = "name"
type = "string"
[[sources]]
url = "https://agenda.example/one"
[recipe]
strategy = "css"
record_selector = "article"
[recipe.fields]
name = "h2::text"
[recipe.pagination]
next = "a[rel=next]::attr(href)"
max_pages = 1
[acquisition]
allowed_hosts = ["agenda.example"]
min_interval_seconds = 0
max_retries = 0
[dataset]
name = "agenda_fiestas"
path = "agenda.sqlite"
identity = ["name"]
"""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app, token = create_app(tmp_path)
    with serving(app, BASE) as http:
        http.headers["X-Studio-Token"] = token
        yield http


def sources(client: TestClient, file: str) -> list[dict[str, Any]]:
    return post(client, "/api/sources", {"file": file}).json()["sources"]


def test_a_cli_load_with_two_invalid_records_reports_rejected_2(tmp_path, client, capsys):
    (tmp_path / "job.toml").write_text(FILE_JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    main(["job", "load", str(tmp_path / "job.toml")])
    assert json.loads(capsys.readouterr().out)["job"]["records_rejected"] == 2
    [metric] = SQLiteDatasetStore(tmp_path / "data.sqlite").run_metrics("items")
    assert (metric.records, metric.rejected, metric.cap) == (2, 2, None)
    [row] = sources(client, "job.toml")
    assert (row["read"], row["rejected"], row["cap"]) == (2, 2, None)
    [group] = job_stats(tmp_path / "job.toml")["groups"]
    assert (group["rejected"], group["capped_runs"]) == (2, 0)


def test_records_of_an_inconsistent_source_count_as_rejected(tmp_path, client):
    rule = '[[consistency]]\nfield = "price"\nkind = "distinct_ratio"\n'
    rule += "min_ratio = 1\nmin_records = 2\n"
    (tmp_path / "job.toml").write_text(FILE_JOB + rule, encoding="utf-8")
    (tmp_path / "page.html").write_text(PAGE.replace(">12<", ">10<"), encoding="utf-8")
    main(["job", "load", str(tmp_path / "job.toml")])
    [metric] = SQLiteDatasetStore(tmp_path / "data.sqlite").run_metrics("items")
    assert metric.rejected == 4  # two invalid plus the two the consistency rule refused
    [row] = sources(client, "job.toml")
    assert (row["status"], row["rejected"]) == ("stale", 4)


def test_a_cli_pagination_run_that_hits_its_cap_is_capped_in_studio(
    tmp_path, client, monkeypatch, capsys
):
    async def resolve(self, host, port):
        return ("8.8.8.8",)

    async def get(self, target, **kwargs):
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        body = b'<article><h2>One</h2></article><a rel="next" href="/two">Next</a>'
        return HttpResponse(200, {"content-type": "text/html"}, body, len(body))

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    (tmp_path / "agenda.toml").write_text(PAGED_JOB, encoding="utf-8")
    assert main(["job", "load", str(tmp_path / "agenda.toml")]) == 3
    capsys.readouterr()
    [metric] = SQLiteDatasetStore(tmp_path / "agenda.sqlite").run_metrics("agenda_fiestas")
    assert (metric.cap, metric.cap_value, metric.rejected) == ("pagination_max_pages", 1, 0)
    [row] = sources(client, "agenda.toml")
    assert row == {
        **row,
        "status": "capped",
        "issue_code": "pagination_max_pages",
        "read": 1,
        "rejected": 0,
        "cap": 1,
    }
    alerts = post(client, "/api/alerts", {"file": "agenda.toml"}).json()["alerts"]
    assert [(a["kind"], a["code"], a["action"]) for a in alerts] == [
        ("cap", "pagination_max_pages", "raise_cap")
    ]
    [group] = job_stats(tmp_path / "agenda.toml")["groups"]
    assert group["capped_runs"] == 1
    [shown] = client.get("/api/projects").json()["projects"]
    assert (shown["name"], shown["dataset"]) == ("Agenda fiestas", "agenda_fiestas")


def test_a_postgres_job_reports_unknown_counts_as_null_not_zero(tmp_path, client):
    text = FILE_JOB.replace('path = "data.sqlite"', 'backend = "postgres"\ndsn_env = "RASTRO_DSN"')
    (tmp_path / "job.toml").write_text(text, encoding="utf-8")
    [row] = sources(client, "job.toml")
    assert (row["status"], row["read"], row["rejected"], row["cap"]) == ("never", None, None, None)
