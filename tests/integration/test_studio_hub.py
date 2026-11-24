"""Studio hub routes: offers for a site, install with a per-site decision, one-step publish."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.applications import Starlette
from starlette.testclient import TestClient

from rastro.studio import routes_registry
from rastro.studio.routes_registry import routes, site_grants
from rastro.studio.web import Studio
from tests.integration.test_studio import serving
from tests.integration.test_studio_registry import JOB, publish
from tests.unit.test_recipe_registry import _write_sample_bundle

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
EXAMPLE = Path(__file__).parents[2] / "examples/event-agenda/sample"
FIELDS = (
    "title", "performers", "venue", "city", "starts_at",
    "ticket_url", "price", "currency", "performance_id",
)  # fmt: skip


@pytest.fixture
def studio(tmp_path: Path) -> Studio:
    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "page.html").write_text("<article class='item'><h2>Evento</h2></article>")
    _write_sample_bundle(tmp_path / "bundle", tmp_path)
    return Studio(tmp_path)


@pytest.fixture
def client(studio: Studio) -> Iterator[TestClient]:
    with serving(Starlette(routes=routes(studio)), "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = studio.token
        yield http


@pytest.mark.parametrize("endpoint", ["offers", "fields", "publish-job"])
def test_guard_before_input(client: TestClient, endpoint: str) -> None:
    path = f"/api/registry/{endpoint}"
    assert client.post(path, json={}, headers={"X-Studio-Token": "wrong"}).status_code == 403
    assert client.post(path, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(path, content="[]").status_code == 400
    assert client.post(path, json={}).status_code == 400


def test_offers_match_the_site_and_stay_candidates(client: TestClient) -> None:
    publish(client)
    found = client.post(
        "/api/registry/offers", json={"url": "https://agenda.example/otra", "registry": "registry"}
    )
    assert found.status_code == 200, found.text
    [offer] = found.json()["offers"]
    assert offer["id"] == "teatro-central-agenda" and offer["state"] == "candidate"
    assert offer["registry"] == "registry"
    none = client.post(
        "/api/registry/offers", json={"url": "https://nada.example/", "registry": "registry"}
    )
    assert none.json()["offers"] == []


@pytest.mark.parametrize("registry", ["../x", "/tmp/x", "http://hub.example/", "missing-dir"])
def test_offers_refuse_bad_registry_and_url(client: TestClient, registry: str) -> None:
    body = {"url": "https://agenda.example/", "registry": registry}
    assert client.post("/api/registry/offers", json=body).status_code == 400
    bad = {"url": "ftp://agenda.example/", "registry": "registry"}
    assert client.post("/api/registry/offers", json=bad).status_code == 400


def test_install_records_a_site_decision_only_when_asked(
    client: TestClient, studio: Studio
) -> None:
    publish(client)
    base = {"registry": "registry", "spec": "teatro-central-agenda@1.0.0", "file": "job.toml"}
    plain = client.post("/api/registry/install", json=base).json()
    assert plain["allowed_for_site"] == [] and site_grants(studio) == {}
    allowed = client.post(
        "/api/registry/install",
        json={**base, "allow": ["page_actions", "stealth"], "site": "https://Agenda.example/x"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["allowed_for_site"] == ["page_actions", "stealth"]
    assert allowed.json()["state"] == "candidate" and allowed.json()["promoted"] is False
    assert site_grants(studio) == {"agenda.example": frozenset({"page_actions", "stealth"})}


@pytest.mark.parametrize(
    "extra",
    [
        {"allow": "stealth", "site": "agenda.example"},
        {"allow": ["nonsense"], "site": "agenda.example"},
        {"allow": ["real_chrome"], "site": "agenda.example"},  # grant-only: never from the page
        {"allow": ["stealth"]},
        {"allow": ["stealth"], "site": "not a host"},
    ],
)
def test_install_refuses_bad_decisions_and_writes_nothing(
    client: TestClient, studio: Studio, tmp_path: Path, extra: dict[str, object]
) -> None:
    publish(client)
    body = {"registry": "registry", "spec": "teatro-central-agenda@1.0.0", "file": "job.toml"}
    assert client.post("/api/registry/install", json={**body, **extra}).status_code == 400
    assert site_grants(studio) == {} and not (tmp_path / "memory.sqlite").exists()


@pytest.fixture
def author(tmp_path: Path, studio: Studio) -> Path:
    from rastro.cli import main

    folder = tmp_path / "author"
    shutil.copytree(EXAMPLE, folder)
    job = folder / "job.toml"
    job.write_text(
        job.read_text(encoding="utf-8").replace(
            "format_version = 1", 'format_version = 1\nrecipe_memory = "recipes.sqlite"'
        ),
        encoding="utf-8",
    )
    assert main(["job", "load", str(job)]) == 0
    return folder


def _publish_body(**over: object) -> dict[str, object]:
    return {
        "file": "author/job.toml", "registry": "hub",
        "sample": "author/teatro-central-cartelera.html",
        "id": "agenda-example", "version": "1.0.0", "author": "Rastro Contributors",
        "license": "Apache-2.0", "descriptions": {n: f"The event's {n}" for n in FIELDS},
        **over,
    }  # fmt: skip


def test_fields_lists_what_must_be_described(client: TestClient, author: Path) -> None:
    got = client.post("/api/registry/fields", json={"file": "author/job.toml"})
    assert got.status_code == 200, got.text
    assert got.json()["fields"] == list(FIELDS)


def test_publish_job_writes_a_proposal_and_pushes_nothing(
    client: TestClient, author: Path, tmp_path: Path
) -> None:
    done = client.post("/api/registry/publish-job", json=_publish_body())
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["pushed"] is False and out["entry"]["id"] == "agenda-example"
    assert [f["name"] for f in out["card"]["fields"]] == list(FIELDS)
    assert (tmp_path / "hub/recipes/agenda-example/1.0.0/card.json").is_file()
    assert out["next_steps"] and all(isinstance(s, str) for s in out["next_steps"])


@pytest.mark.parametrize(
    "over",
    [
        {"descriptions": {"title": "only one"}},
        {"descriptions": {}},
        {"descriptions": [1]},
        {"sample": ""},
        {"sample": "author/missing.html"},
        {"sample": "../escape.html"},
        {"registry": "https://hub.example/"},
        {"registry": "../hub"},
        {"file": "../job.toml"},
        {"source": 0},
    ],
)
def test_publish_job_refusals_write_nothing(
    client: TestClient, author: Path, tmp_path: Path, over: dict[str, object]
) -> None:
    response = client.post("/api/registry/publish-job", json=_publish_body(**over))
    assert response.status_code == 400 and response.json()["ok"] is False
    assert not (tmp_path / "hub").exists()


def test_module_exposes_no_global_grants() -> None:
    assert not hasattr(routes_registry, "SITE_GRANTS")
