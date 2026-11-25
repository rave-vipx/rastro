"""Studio guided create: estimate, the site's own data, pick candidates and exploration."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.navigation_explorer import ExplorationEvent, ExplorationResult
from rastro.domain.models import CssRecipe
from rastro.ports.engine import CapturedResponse
from rastro.studio import wizard_more
from rastro.studio.app import create_app
from tests.integration.test_studio import post, serving
from tests.integration.test_studio_pick import URL, Browser, Namer, started

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

JOB = """format_version = 1

[acquisition]
allowed_hosts = ["shop.example"]

[[sources]]
kind = "url"
url = "https://shop.example/items"

[recipe]
strategy = "css"
record_selector = "article.card"

[recipe.fields]
name = "h3.name::text"

[[fields]]
name = "name"
type = "string"
"""
ITEMS = {"data": {"items": [{"name": "Lamp", "price": 10}, {"name": "Desk", "price": 99}]}}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    yield from started(tmp_path, Browser())


def test_estimate_for_a_url_and_for_a_bounded_crawl(client: TestClient) -> None:
    one = post(client, "/api/wizard/estimate", {"url": URL}).json()
    assert one["ok"] and one["requests_max"] == 1
    more = post(client, "/api/wizard/estimate", {"url": URL, "more": True}).json()
    assert more["requests_max"] > 1 and more["minutes_max"] >= more["minutes_min"] > 0


def test_estimate_for_a_saved_job_stays_inside_the_folder(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "shop.toml").write_text(JOB, encoding="utf-8")
    assert post(client, "/api/wizard/estimate", {"file": "shop.toml"}).json()["requests_max"] == 1
    for name in ("../shop.toml", "/etc/passwd"):
        refused = post(client, "/api/wizard/estimate", {"file": name})
        assert refused.status_code in {400, 403, 404} and refused.json()["ok"] is False


def test_offers_name_host_path_records_and_covered_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.dumps(ITEMS).encode()
    captured = (
        CapturedResponse("shop.example", "/api/items", "GET", 200, "application/json", body),
        CapturedResponse("shop.example", "/api/empty", "GET", 200, "application/json", b"{}"),
    )
    monkeypatch.setattr(wizard_more, "_captured", lambda url, studio: captured)
    out = post(client, "/api/wizard/offers", {"url": URL, "fields": ["name", "price"]}).json()
    assert out["ok"] and out["data_trust"] == "untrusted_source_data" and out["browser"]
    assert out["offers"] == [
        {"host": "shop.example", "path": "/api/items", "records": 2,
         "record_path": "/data/items", "covers": ["name", "price"]}
    ]  # fmt: skip


def test_offers_without_a_browser_are_an_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(url: str, studio: object) -> object:
        raise ImportError("no browser")

    monkeypatch.setattr(wizard_more, "_captured", missing)
    out = post(client, "/api/wizard/offers", {"url": URL}).json()
    assert out["ok"] and out["offers"] == [] and out["browser"] is False
    assert post(client, "/api/wizard/offers", {"url": "file:///etc/passwd"}).status_code == 400


def test_page_lists_clickable_elements_of_the_first_record(client: TestClient) -> None:
    out = post(
        client, "/api/wizard/page",
        {"url": URL, "fields": {"name": "h3.name"}, "record_selector": "article.card"},
    ).json()  # fmt: skip
    texts = [item["text"] for item in out["elements"]]
    assert texts == ["Lamp", "10.50"]
    assert out["elements"][0]["path"] == [0, 0, 0, 0]  # html > body > main > article > h3
    assert out["elements"][0]["boxes"] == [{"x": 10, "y": 20, "w": 100, "h": 30}]
    assert "locator" not in out["elements"][0]


def _result(steps: bool) -> ExplorationResult:
    clicks = [{"action": "click", "selector": "button.more"}] if steps else []
    recipe = CssRecipe.model_validate(
        {"record_selector": "article.card", "fields": {"name": "h3.name::text"}, "steps": clicks}
    )
    events = (
        ExplorationEvent(kind="observed", records=3),
        ExplorationEvent(kind="kept", records=5, added=2, action="click", target="More"),
        ExplorationEvent(kind="stopped", records=5, reason="model_done"),
    )
    return ExplorationResult(
        recipe=recipe, records_before=3, records_after=5 if steps else 3,
        stop_reason="model_done", model_calls=2, events=events,
    )  # fmt: skip


def test_explore_narrates_and_keeps_only_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "shop.toml").write_text(JOB, encoding="utf-8")

    def fake(job: Any, model: object, allow_remote: bool) -> Any:
        rendered = job.model_copy(
            update={"acquisition": job.acquisition.model_copy(update={"render": "browser"})}
        )
        return rendered, _result(True)

    monkeypatch.setattr(wizard_more, "_explore", fake)
    for http in started(tmp_path, Browser(), model=Namer("{}")):
        assert post(http, "/api/wizard/explore/keep", {"file": "shop.toml"}).status_code == 409
        out = post(http, "/api/wizard/explore", {"file": "shop.toml"}).json()
        assert out.get("kept"), out
        assert out["before"] == 3 and out["after"] == 5
        assert out["events"][1]["message"] == "clicked 'More': +2 records"
        assert (tmp_path / "shop.toml").read_text(encoding="utf-8") == JOB  # nothing written yet
        kept = post(http, "/api/wizard/explore/keep", {"file": "shop.toml"}).json()
        assert kept["ok"] and len(kept["sha256"]) == 64
        saved = (tmp_path / "shop.toml").read_text(encoding="utf-8")
        assert 'render = "browser"' in saved and "button.more" in saved


def test_explore_needs_a_model_and_consent_for_a_remote_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "shop.toml").write_text(JOB, encoding="utf-8")
    calls: list[bool] = []

    def fake(job: Any, model: object, allow_remote: bool) -> Any:
        calls.append(allow_remote)
        return job, _result(False)

    monkeypatch.setattr(wizard_more, "_explore", fake)
    for http in started(tmp_path, Browser()):
        out = post(http, "/api/wizard/explore", {"file": "shop.toml"})
        assert out.status_code == 409 and out.json()["error"] == "no_model_configured"
    for http in started(tmp_path, Browser(), model=Namer("{}"), model_remote=True):
        out = post(http, "/api/wizard/explore", {"file": "shop.toml"})
        assert out.status_code == 409 and calls == []
        body = {"file": "shop.toml", "allow_remote": True}
        assert post(http, "/api/wizard/explore", body).json()["kept"] is False
        assert calls == [True]
        assert post(http, "/api/wizard/explore", {"file": "../x.toml", "allow_remote": True})
        assert post(http, "/api/wizard/explore/keep", {"file": "shop.toml"}).status_code == 409


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/wizard/estimate", {"url": URL}),
        ("/api/wizard/offers", {"url": URL}),
        ("/api/wizard/explore", {"file": "shop.toml"}),
        ("/api/wizard/explore/keep", {"file": "shop.toml"}),
    ],
)
def test_new_routes_require_token_and_origin(
    tmp_path: Path, path: str, body: dict[str, object]
) -> None:
    browser = Browser()
    app, token = create_app(tmp_path, acquisition=browser)
    with serving(app, "http://127.0.0.1:8765") as http:
        assert http.post(path, json=body).status_code == 403
        cross = {"X-Studio-Token": token, "Origin": "http://127.0.0.1:9999"}
        assert http.post(path, json=body, headers=cross).status_code == 403
        assert http.get(path, headers={"X-Studio-Token": token}).status_code == 405
    assert browser.fetched == []


def test_suggest_says_when_exploring_could_find_more(client: TestClient) -> None:
    body = {"url": URL, "examples": [{"name": "Lamp"}]}
    assert post(client, "/api/suggest", body).json()["explore"] is None  # 3 of 2 wanted
    more = {**body, "url": URL, "records_min": 3}
    assert post(client, "/api/suggest", more).json()["explore"] is None  # exactly as many
