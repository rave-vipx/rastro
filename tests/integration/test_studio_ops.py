"""Studio wizard, run and dataset endpoints."""

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.job_loader import load_job
from rastro.application.operator_trust import operator_trust
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.models import Outcome, Representation, Source
from rastro.studio.app import create_app
from tests.integration.test_job_preview import PAGE
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

EXAMPLES = [{"title": "Tango", "venue": "Sala Alba"}]
URL = "https://agenda.example.com/events"
QUERY_URL = "https://shop.example/search?q=lamp&page=2"
HTTP_URL = "http://agenda.example.com/events"


class FakeAcquirer:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        self.fetched.append(source.url)
        body = self.pages.get(source.url)
        if body is None:
            return AcquisitionResult(
                url=source.url,
                body=None,
                status=Outcome.PERMANENT_FAILURE,
                issue_code="http_404",
                http_status=404,
                requests=1,
            )
        return AcquisitionResult(
            url=source.url,
            body=body,
            status=Outcome.SUCCESS,
            issue_code=None,
            http_status=200,
            requests=1,
            bytes_received=len(body.encode("utf-8")),
        )


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
    """The wizard lists .html/.htm pages below the root, skipping hidden folders."""
    assert client.get("/api/pages").json() == {"files": ["pages/agenda.html"], "truncated": False}


def test_create_from_url_fetches_and_writes_a_url_job(tmp_path: Path) -> None:
    """Studio accepts a URL, fetches it, and writes a job that scrapes that URL."""
    acquirer = FakeAcquirer({URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        body = {"file": "agenda.toml", "url": URL, "examples": EXAMPLES}
        found = post(http, "/api/suggest", body).json()
        assert found["ok"] and found["records"] == 3
        assert found["allow_http"] is False
        made = post(
            http, "/api/create", {**body, "dataset": "agenda.sqlite", "identity": ["title"]}
        )
    assert made.status_code == 200, made.text
    assert made.json()["allow_http"] is False
    assert acquirer.fetched == [URL]  # create reuses the suggest fetch
    job = load_job(tmp_path / "agenda.toml")
    assert isinstance(job.sources[0], UrlJobSource)
    assert job.sources[0].url == URL
    assert job.acquisition is not None
    assert job.acquisition.allowed_hosts == ("agenda.example.com",)
    assert not list(tmp_path.rglob("*.html"))


def test_create_from_url_keeps_the_query_string(tmp_path: Path) -> None:
    """Listing/search URLs keep ?q= so the job scrapes the same page the preview used."""
    acquirer = FakeAcquirer({QUERY_URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        body = {"file": "shop.toml", "url": QUERY_URL, "examples": EXAMPLES}
        assert post(http, "/api/suggest", body).status_code == 200
        made = post(http, "/api/create", body)
    assert made.status_code == 200, made.text
    assert acquirer.fetched == [QUERY_URL]
    job = load_job(tmp_path / "shop.toml")
    assert job.sources[0].url == QUERY_URL
    text = (tmp_path / "shop.toml").read_text()
    assert "q=lamp" in text
    assert "Could not" not in text


def test_create_from_http_url_reports_allow_http(tmp_path: Path) -> None:
    """An http:// source writes allow_http and Studio reports it."""
    acquirer = FakeAcquirer({HTTP_URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        body = {"file": "plain.toml", "url": HTTP_URL, "examples": EXAMPLES}
        found = post(http, "/api/suggest", body).json()
        assert found["allow_http"] is True
        made = post(http, "/api/create", body).json()
    assert made["allow_http"] is True
    assert load_job(tmp_path / "plain.toml").acquisition.allow_http is True


def test_create_from_url_respects_operator_port_and_private_grants(tmp_path: Path) -> None:
    """Non-standard ports and private hosts need the operator's grants."""
    private = "http://192.168.1.10/list"
    custom = "https://shop.example:8443/list"
    acquirer = FakeAcquirer({private: PAGE, custom: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        denied = post(http, "/api/suggest", {"url": custom, "examples": EXAMPLES})
        assert denied.status_code == 400
        assert "8443" not in denied.text and "shop.example" not in denied.text
        with operator_trust(allow_ports=[8443]):
            ok = post(http, "/api/suggest", {"url": custom, "examples": EXAMPLES})
        assert ok.status_code == 200, ok.text
        private_denied = post(http, "/api/suggest", {"url": private, "examples": EXAMPLES})
        assert private_denied.status_code == 400
        with operator_trust(allow_private_network=True):
            private_ok = post(http, "/api/suggest", {"url": private, "examples": EXAMPLES})
        assert private_ok.status_code == 200, private_ok.text


def test_create_from_url_refuses_without_a_url(tmp_path: Path) -> None:
    """Creating a job without a URL or a saved page is refused."""
    app, token = create_app(tmp_path, acquisition=FakeAcquirer({}))
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        reply = post(http, "/api/create", {"file": "a.toml", "examples": EXAMPLES})
    assert reply.status_code == 400
    assert not (tmp_path / "a.toml").exists()


def test_wizard_page_is_url_first(tmp_path: Path) -> None:
    """The Studio page asks for a URL; it does not require uploading HTML."""
    app, _token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as http:
        page = http.get("/").text
    assert 'id="page-url"' in page
    assert "Save the web page as HTML" not in page
    assert "Paste a page URL" in page or "Page URL" in page


def test_suggest_returns_recipe_preview_and_confidence(client: TestClient) -> None:
    """Suggest verifies a recipe and shows per-field confidence; nothing is written."""
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
    """Create writes a job with a dataset; a second create never overwrites it."""
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
    """The wizard can enable recipe memory, so repair works later."""
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
    """The dataset and recipe store cannot be the job, the page or each other."""
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
    """The dataset path is relative to the job file and its folder must exist."""
    body = {"file": "pages/a.toml", "page": "pages/agenda.html", "examples": EXAMPLES}
    answer = post(client, "/api/create", {**body, "dataset": dataset, "identity": ["title"]})
    assert answer.status_code == 400 and code in json.dumps(answer.json())
    assert not (root / "pages/a.toml").exists()


def test_extract_writes_nothing_and_load_fills_the_dataset(client: TestClient, root: Path) -> None:
    """Extract is read-only; load writes the dataset and reports origins."""
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
    """Bounded pages with stale markers; JSON and HTML export of the same records."""
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
    bad = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "pdf"})
    assert bad.status_code == 400


def test_new_endpoints_require_token_and_origin(root: Path) -> None:
    """Every POST needs the token and this Studio's exact origin."""
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        for path in (
            "/api/suggest",
            "/api/create",
            "/api/extract",
            "/api/load",
            "/api/records",
            "/api/export",
            "/api/changes",
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


@pytest.mark.parametrize("path", ["/api/extract", "/api/load"])
def test_job_declared_proxy_needs_the_operator_flag(root: Path, path: str) -> None:
    """Audit H1: `rastro studio` without --allow-egress refuses a job's own proxy."""
    from tests.integration.test_mcp_server import _PROXY_JOB

    (root / "proxy.toml").write_text(_PROXY_JOB, encoding="utf-8")
    for allow, expected in ((False, "allow-egress"), (True, "RASTRO_ALLOWED_ENV")):
        app, token = create_app(root, allow_egress=allow)
        with serving(app, "http://127.0.0.1:8765") as http:
            http.headers["X-Studio-Token"] = token
            reply = post(http, path, {"file": "proxy.toml"})
        assert reply.status_code >= 400 and expected in reply.text


def test_export_csv_and_xlsx_flatten_nested_columns(client: TestClient) -> None:
    """N5: one-click CSV and XLSX use the shared flattened table; XLSX travels as base64."""
    create(client, dataset="agenda.sqlite", identity=["title"])
    post(client, "/api/load", {"file": "pages/agenda.toml"})
    csv_ = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "csv"}).json()
    assert csv_["filename"] == "agenda.csv" and csv_["media_type"] == "text/csv"
    assert csv_["content"].splitlines()[0] == "title,venue,last_observed_at"
    xlsx = post(client, "/api/export", {"file": "pages/agenda.toml", "format": "xlsx"}).json()
    assert xlsx["encoding"] == "base64" and xlsx["filename"] == "agenda.xlsx"
    assert base64.b64decode(xlsx["content"])[:2] == b"PK"


def test_changes_is_a_first_run_until_a_second_load(client: TestClient) -> None:
    """N5: no earlier load, nothing to compare; later loads count new and removed records."""
    create(client, dataset="agenda.sqlite", identity=["title"])
    post(client, "/api/load", {"file": "pages/agenda.toml"})
    first = post(client, "/api/changes", {"file": "pages/agenda.toml"}).json()
    assert first["ok"] and first["first_run"] is True and first["partial"] == []
    post(client, "/api/load", {"file": "pages/agenda.toml"})
    second = post(client, "/api/changes", {"file": "pages/agenda.toml"}).json()
    assert second["first_run"] is False and second["new"] == 0 and second["removed"] == 0
    page = post(client, "/api/records", {"file": "pages/agenda.toml"}).json()
    assert all("change" not in row for row in page["records"])
    assert post(client, "/api/changes", {"file": "../x.toml"}).status_code == 400
