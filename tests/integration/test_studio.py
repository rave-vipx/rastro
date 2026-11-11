"""Local Studio (P-008): endpoints, confinement, Origin/token rejection, atomic accept."""

import json
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from httpx2 import Response
from starlette.applications import Starlette
from starlette.testclient import TestClient

from rastro.application.job_loader import load_job
from rastro.cli import main
from rastro.studio.app import MAX_BODY, create_app
from rastro.studio.editing import SelectorEdit, apply_edit
from tests.conftest import _ORIGINAL_SOCKET
from tests.integration.test_job_preview import JOB, PAGE


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "page.html").write_text(PAGE)
    (tmp_path / "job.toml").write_text("# keep me\n" + JOB)
    return tmp_path


@contextmanager
def serving(app: Starlette, base_url: str) -> Iterator[TestClient]:
    """In-process client; only the event loop's private wake-up socket is allowed to open."""
    client = TestClient(app, base_url=base_url)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "socket", _ORIGINAL_SOCKET)
        client.__enter__()
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


def post(client: TestClient, path: str, body: dict[str, object], **headers: str) -> Response:
    return client.post(path, json=body, headers=headers)


def test_page_lists_validates_previews_and_connects(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert client.headers["X-Studio-Token"] not in page.text
    assert "__STUDIO_TOKEN__" not in page.text
    assert client.get("/static/studio.js").status_code == 200
    assert client.get("/static/other.js").status_code == 404
    assert client.get("/api/jobs").json()["files"] == ["job.toml"]
    checked = post(client, "/api/validate", {"file": "job.toml"}).json()
    assert checked["ok"] and checked["has_dataset"]
    assert {"source": None, "field": "title", "selector": "b::text"} in checked["selectors"]
    shown = post(client, "/api/preview", {"file": "job.toml"}).json()
    record = shown["preview"]["sources"][0]["records"][0]
    assert record["status"] == "accepted"
    assert {f["name"] for f in record["fields"]} >= {"title", "venue"}
    commands = client.get("/api/connect", params={"file": "job.toml"}).json()
    assert "rastro job load job.toml" in commands["cli"]
    assert "rastro dataset export agenda.sqlite --format json" in commands["cli"]
    assert "run_job_file('job.toml')" in commands["sdk"]


def test_invalid_job_is_a_structured_error(client: TestClient, root: Path) -> None:
    (root / "bad.toml").write_text("format_version = 2\n")
    reply = post(client, "/api/validate", {"file": "bad.toml"})
    assert reply.status_code == 400
    assert reply.json()["ok"] is False


def test_correct_preview_then_accept_writes_atomically_and_revalidates(
    client: TestClient, root: Path
) -> None:
    sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
    edit = {"source": None, "field": "venue", "selector": "s::text"}
    shown = post(client, "/api/preview", {"file": "job.toml", "edit": edit}).json()
    venue = next(
        f for f in shown["preview"]["sources"][0]["records"][0]["fields"] if f["name"] == "venue"
    )
    assert venue["value"].startswith("2026-")
    assert 'venue = "s::text"' not in (root / "job.toml").read_text()  # preview writes nothing
    done = post(client, "/api/accept", {"file": "job.toml", "edit": edit, "sha256": sha})
    assert done.status_code == 200
    text = (root / "job.toml").read_text()
    assert text.startswith("# keep me\n") and "venue = " in text
    assert load_job(root / "job.toml").recipe.fields["venue"] == "s::text"  # type: ignore[union-attr]
    assert sorted(p.name for p in root.iterdir()) == ["job.toml", "page.html"]


def test_accept_refuses_stale_digest_and_missing_edit(client: TestClient, root: Path) -> None:
    before = (root / "job.toml").read_bytes()
    edit = {"source": None, "field": "venue", "selector": "s::text"}
    stale = post(client, "/api/accept", {"file": "job.toml", "edit": edit, "sha256": "0" * 64})
    assert stale.status_code == 409
    assert post(client, "/api/accept", {"file": "job.toml", "sha256": "x"}).status_code == 400
    assert (root / "job.toml").read_bytes() == before


def test_invalid_edit_never_reaches_disk(client: TestClient, root: Path) -> None:
    before = (root / "job.toml").read_bytes()
    sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
    for edit in (
        {"source": None, "field": "venue", "selector": "no-pseudo"},
        {"source": None, "field": "nope", "selector": "i::text"},
        {"source": 7, "field": "venue", "selector": "i::text"},
        {"source": None, "field": "venue", "selector": ""},
        {"source": True, "field": "venue", "selector": "i::text"},
    ):
        reply = post(client, "/api/accept", {"file": "job.toml", "edit": edit, "sha256": sha})
        assert reply.status_code == 400
    assert (root / "job.toml").read_bytes() == before


def test_path_escape_and_symlinks_are_refused(
    client: TestClient, root: Path, tmp_path: Path
) -> None:
    outside = root.parent / "outside.toml"
    outside.write_text(JOB)
    try:
        (root / "link.toml").symlink_to(outside)
        os.symlink(root.parent, root / "dir")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    for name in (
        "../outside.toml",
        "/etc/passwd.toml",
        "link.toml",
        "dir/outside.toml",
        "a\\b.toml",
        "job.txt",
        "page.html",
        "missing.toml",
        "",
    ):
        reply = post(client, "/api/validate", {"file": name})
        assert reply.status_code == 400, name
    assert client.get("/api/connect", params={"file": "../outside.toml"}).status_code == 400
    assert client.get("/api/jobs").json()["files"] == ["job.toml"]
    edit = {"source": None, "field": "title", "selector": "i::text"}
    assert (
        post(client, "/api/accept", {"file": "link.toml", "edit": edit, "sha256": "x"}).status_code
        == 400
    )
    assert outside.read_text() == JOB


def test_host_origin_and_token_are_enforced(root: Path) -> None:
    app, token = create_app(root)
    with serving(app, "http://evil.example") as other_host:
        assert other_host.get("/api/jobs").status_code == 400
    with serving(app, "http://localhost:8765") as local:
        _check_local(local, token)


def _check_local(local: TestClient, token: str) -> None:
    assert local.get("/api/jobs").status_code == 200
    assert local.get("/api/jobs", headers={"Origin": "https://evil.example"}).status_code == 403
    # Another local app (other port) is not this Studio's origin.
    assert local.get("/api/jobs", headers={"Origin": "http://localhost:3000"}).status_code == 403
    assert local.get("/api/jobs", headers={"Origin": "http://localhost:8765"}).status_code == 200
    body = {"file": "job.toml"}
    assert local.post("/api/validate", json=body).status_code == 403
    assert (
        local.post("/api/validate", json=body, headers={"X-Studio-Token": "wrong"}).status_code
        == 403
    )
    cross = {"X-Studio-Token": token, "Origin": "https://evil.example"}
    assert local.post("/api/validate", json=body, headers=cross).status_code == 403
    assert (
        local.post("/api/validate", json=body, headers={"X-Studio-Token": token}).status_code == 200
    )


def test_request_bodies_are_bounded_and_must_be_objects(client: TestClient) -> None:
    big = client.post("/api/validate", content=b'{"file": "' + b"a" * MAX_BODY + b'"}')
    assert big.status_code == 413
    assert client.post("/api/validate", content=b"[1]").status_code == 400
    assert client.post("/api/validate", content=b"{").status_code == 400
    assert client.post("/api/validate", json={}).status_code == 400


def test_source_content_is_rendered_as_text_only() -> None:
    script = (Path(__file__).parents[2] / "src/rastro/studio/static/studio.js").read_text()
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "document.write" not in script and "eval(" not in script


def test_json_job_is_edited_and_roundtrips(root: Path) -> None:
    job = json.loads(
        json.dumps(
            {
                "format_version": 1,
                "sources": [
                    {
                        "path": "page.html",
                        "url": "https://agenda.example.com/events",
                        "recipe": {
                            "strategy": "css",
                            "record_selector": "li",
                            "fields": {"title": "b::text"},
                        },
                    }
                ],
                "fields": [{"name": "title", "type": "string"}],
            }
        )
    )
    (root / "j.json").write_text(json.dumps(job))
    new = apply_edit((root / "j.json").read_text(), ".json", SelectorEdit("title", "i::text", 0))
    assert json.loads(new)["sources"][0]["recipe"]["fields"]["title"] == "i::text"


def test_ambiguous_toml_selector_is_refused() -> None:
    text = (
        'format_version = 1\n[recipe]\nstrategy = "css"\nrecord_selector = "li"\n'
        'fields = { a = "b::text", b = "b::text" }\n'
    )
    with pytest.raises(Exception, match="not unique"):
        apply_edit(text, ".toml", SelectorEdit("a", "i::text"))


def test_cli_without_extra_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["studio"]) == 2
    assert "missing_extra" in capsys.readouterr().err


def test_cli_serves_loopback_only(
    monkeypatch: pytest.MonkeyPatch, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uvicorn = pytest.importorskip("uvicorn")
    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    assert main(["studio", "--root", str(root), "--port", "9123"]) == 0
    assert "http://127.0.0.1:9123/#token=" in capsys.readouterr().err
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9123
    assert main(["studio", "--root", str(root), "--port", "0"]) != 0
    assert main(["studio", "--root", str(root / "nope")]) != 0


def test_listing_skips_hidden_and_vendor_dirs_and_reports_truncation(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "site"):
        (root / name).mkdir()
        (root / name / "x.json").write_text("{}")
    (root / "ok").mkdir()
    (root / "ok" / "y.json").write_text("{}")
    listing = client.get("/api/jobs").json()
    assert listing["files"] == ["job.toml", "ok/y.json"] and listing["truncated"] is False
    monkeypatch.setattr("rastro.studio.files.MAX_FILES", 1)
    cut = client.get("/api/jobs").json()
    assert cut["files"] == ["job.toml"] and cut["truncated"] is True


def test_script_reads_token_from_fragment_only() -> None:
    base = Path(__file__).parents[2] / "src/rastro/studio/static"
    script = (base / "studio.js").read_text()
    assert "location.hash" in script and "replaceState" in script
    assert "studio-token" not in (base / "index.html").read_text()
