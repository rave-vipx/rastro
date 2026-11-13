"""Studio wizard, run and dataset endpoints (P-008, INT-002, JOB-001, DATA-001, DATA-004)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.job_loader import load_job
from rastro.studio.app import create_app
from tests.integration.test_job_preview import PAGE
from tests.integration.test_studio import post, serving

EXAMPLES = [{"title": "Tango", "venue": "Sala Verdi"}]
URL = "https://agenda.example.com/events"


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "agenda.html").write_text(PAGE)
    (tmp_path / "notes.txt").write_text("not a page")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.html").write_text(PAGE)
    return tmp_path


def create(client: TestClient, **extra: object) -> dict[str, object]:
    body = {"file": "pages/agenda.toml", "page": "pages/agenda.html", "url": URL}
    reply = post(client, "/api/create", {**body, "examples": EXAMPLES, **extra})
    assert reply.status_code == 200, reply.text
    data: dict[str, object] = reply.json()
    return data


def test_pages_lists_saved_html_only(client: TestClient) -> None:
    """P-008: the wizard lists .html/.htm pages below the root, skipping hidden folders."""
    assert client.get("/api/pages").json() == {"files": ["pages/agenda.html"], "truncated": False}


def test_suggest_returns_recipe_preview_and_confidence(client: TestClient) -> None:
    """INT-002: suggest verifies a recipe and shows per-field confidence; nothing is written."""
    body = {"page": "pages/agenda.html", "url": URL, "examples": EXAMPLES}
    found = post(client, "/api/suggest", body).json()
    assert found["ok"] and found["records"] == 3 and found["data_trust"] == "untrusted_source_data"
    assert set(found["recipe"]["fields"]) == {"title", "venue"}
    assert found["confidence"]["title"]["method"] == "example"
    assert {"title": "Candombe", "venue": "Teatro Solis"} in found["preview"]


@pytest.mark.parametrize(
    "body",
    [
        {"page": "../agenda.html", "examples": EXAMPLES},
        {"page": "notes.txt", "examples": EXAMPLES},
        {"page": "pages/agenda.html", "examples": []},
        {"page": "pages/agenda.html", "examples": [{"title": 3}]},
        {"page": "pages/agenda.html", "examples": [{"1bad": "Tango"}]},
        {"page": "pages/agenda.html", "examples": [{"title": "Nowhere"}]},
        {"page": "pages/agenda.html", "examples": EXAMPLES, "records_min": True},
    ],
)
def test_suggest_rejects_bad_input(client: TestClient, body: dict[str, object]) -> None:
    assert post(client, "/api/suggest", body).status_code == 400


def test_create_writes_a_valid_job_and_refuses_overwrite(client: TestClient, root: Path) -> None:
    """JOB-001: create writes a job with a dataset; a second create never overwrites it."""
    made = create(client, dataset="agenda.sqlite", identity=["title"])
    assert made["file"] == "pages/agenda.toml" and len(str(made["sha256"])) == 64
    job = load_job(root / "pages/agenda.toml")
    assert job.dataset is not None and job.dataset.identity == ("title",)
    before = (root / "pages/agenda.toml").read_bytes()
    again = post(
        client,
        "/api/create",
        {"file": "pages/agenda.toml", "page": "pages/agenda.html", "examples": EXAMPLES},
    )
    assert again.status_code == 400
    assert (root / "pages/agenda.toml").read_bytes() == before


@pytest.mark.parametrize(
    "name",
    ["../x.toml", "/tmp/x.toml", "x.json", "a b.toml", "missing/x.toml", ".x.toml", "link/x.toml"],
)
def test_create_validates_the_name(client: TestClient, root: Path, name: str) -> None:
    (root / "link").symlink_to(root / "pages")
    body = {"file": name, "page": "pages/agenda.html", "examples": EXAMPLES}
    assert post(client, "/api/create", body).status_code == 400
    assert not list((root / "pages").glob("*.toml"))


def test_create_can_add_recipe_memory(client: TestClient, root: Path) -> None:
    """REC-006, JOB-001: the wizard can enable recipe memory, so repair works later."""
    create(client, dataset="agenda.sqlite", identity=["title"], recipe_memory="recipes.sqlite")
    assert load_job(root / "pages/agenda.toml").recipe_memory == "recipes.sqlite"
    body = {"file": "pages/b.toml", "page": "pages/agenda.html", "examples": EXAMPLES}
    for bad in ("missing/r.sqlite", "../r.sqlite", "/tmp/r.sqlite"):
        assert post(client, "/api/create", {**body, "recipe_memory": bad}).status_code == 400
    assert not (root / "pages/b.toml").exists()


@pytest.mark.parametrize(
    ("dataset", "memory"),
    [
        (None, "agenda.toml"),
        (None, "./agenda.html"),
        ("d.sqlite", "./d.sqlite"),
        ("agenda.html", None),
    ],
)
def test_create_refuses_paths_that_name_the_same_file(
    client: TestClient, root: Path, dataset: str | None, memory: str | None
) -> None:
    """JOB-001: the dataset and recipe store cannot be the job, the page or each other."""
    body = {"file": "pages/agenda.toml", "page": "pages/agenda.html", "examples": EXAMPLES}
    extra = {"dataset": dataset, "identity": ["title"] if dataset else [], "recipe_memory": memory}
    assert post(client, "/api/create", {**body, **extra}).status_code == 400
    assert not (root / "pages/agenda.toml").exists()


def test_create_requires_identity_with_a_dataset(client: TestClient, root: Path) -> None:
    body = {"file": "pages/a.toml", "page": "pages/agenda.html", "examples": EXAMPLES}
    assert post(client, "/api/create", {**body, "dataset": "a.sqlite"}).status_code == 400
    assert post(client, "/api/create", {**body, "identity": "title"}).status_code == 400
    assert not (root / "pages/a.toml").exists()


@pytest.mark.parametrize(
    ("dataset", "code"),
    [
        ("../outside.sqlite", "dataset_outside_job_folder"),
        ("missing/a.sqlite", "dataset_folder_missing"),
    ],
)
def test_create_refuses_a_dataset_the_job_could_not_open(
    client: TestClient, root: Path, dataset: str, code: str
) -> None:
    """DATA-001: the dataset path is relative to the job file and its folder must exist."""
    body = {"file": "pages/a.toml", "page": "pages/agenda.html", "examples": EXAMPLES}
    answer = post(client, "/api/create", {**body, "dataset": dataset, "identity": ["title"]})
    assert answer.status_code == 400 and code in json.dumps(answer.json())
    assert not (root / "pages/a.toml").exists()


def test_extract_writes_nothing_and_load_fills_the_dataset(client: TestClient, root: Path) -> None:
    """DATA-001, REC-006: extract is read-only; load writes the dataset and reports origins."""
    create(client, dataset="agenda.sqlite", identity=["title"])
    job = root / "pages/agenda.toml"
    job.write_text(
        job.read_text().replace(
            "format_version = 1", 'format_version = 1\nrecipe_memory = "r.sqlite"'
        )
    )
    shown = post(client, "/api/extract", {"file": "pages/agenda.toml"}).json()
    assert shown["job"]["status"] == "success" and shown["sources"][0]["records"] == 3
    assert shown["sources"][0]["recipe_origin"] == "job"
    assert sorted(p.name for p in (root / "pages").iterdir()) == ["agenda.html", "agenda.toml"]
    loaded = post(client, "/api/load", {"file": "pages/agenda.toml", "drift": True}).json()
    result = loaded["result"]
    assert result["job"]["status"] == "success" and result["sources"][0]["counts"]["created"] == 3
    assert result["sources"][0]["recipe_origin"] == "job" and result["drift"] is not None
    assert post(client, "/api/load", {"file": "pages/agenda.toml", "drift": 1}).status_code == 400


def test_records_page_and_export(client: TestClient) -> None:
    """DATA-004: bounded pages with stale markers; JSON and HTML export of the same records."""
    create(client, dataset="agenda.sqlite", identity=["title"])
    assert post(client, "/api/records", {"file": "pages/agenda.toml"}).status_code == 400
    post(client, "/api/load", {"file": "pages/agenda.toml"})
    page = post(client, "/api/records", {"file": "pages/agenda.toml", "limit": 2}).json()
    assert page["total"] == 3 and len(page["records"]) == 2 and page["fields"] == ["title", "venue"]
    assert page["records"][0]["stale"] is False and page["data_trust"] == "untrusted_source_data"
    rest = post(client, "/api/records", {"file": "pages/agenda.toml", "offset": 2}).json()
    assert len(rest["records"]) == 1
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "5"}):
        assert post(client, "/api/records", {"file": "pages/agenda.toml", **bad}).status_code == 400
    exported = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "json"}).json()
    assert exported["filename"].endswith(".json")
    assert len(json.loads(exported["content"])["records"]) == 3
    html = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "html"}).json()
    assert html["media_type"] == "text/html" and "Candombe" in html["content"]
    bad = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "csv"})
    assert bad.status_code == 400


def test_new_endpoints_require_token_and_origin(root: Path) -> None:
    """P-008: every POST needs the token and this Studio's exact origin."""
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        for path in (
            "/api/suggest",
            "/api/create",
            "/api/extract",
            "/api/load",
            "/api/records",
            "/api/export",
            "/api/health",
            "/api/repair",
            "/api/promote",
            "/api/apply",
        ):
            assert http.post(path, json={}).status_code == 403, path
            cross = {"X-Studio-Token": token, "Origin": "http://127.0.0.1:9999"}
            assert http.post(path, json={}, headers=cross).status_code == 403, path
            assert http.get(path).status_code == 405, path
        assert http.get("/api/pages", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not list(root.rglob("*.toml"))
