"""Studio hardening: token on every API route, write-time digest, capped values (P-008)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application import recipe_apply
from rastro.studio import editing, routes_dataset
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB as API_JOB
from tests.integration.test_job_api import board
from tests.integration.test_job_preview import JOB, PAGE
from tests.integration.test_studio import post, serving

GETS = ("/api/jobs", "/api/pages", "/api/connect?file=job.toml")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "page.html").write_text(PAGE)
    (tmp_path / "job.toml").write_text(JOB)
    return tmp_path


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


@pytest.mark.parametrize("path", GETS)
def test_api_gets_require_the_token(root: Path, path: str) -> None:
    """P-008: every /api route needs the token, GETs included; page and assets stay public."""
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        assert http.get(path).status_code == 403
        assert http.get(path, headers={"X-Studio-Token": "wrong"}).status_code == 403
        assert http.get(path, headers={"X-Studio-Token": token}).status_code == 200
        cross = {"X-Studio-Token": token, "Origin": "http://127.0.0.1:9999"}
        assert http.get(path, headers=cross).status_code == 403
        assert http.get("/").status_code == 200
        assert http.get("/static/studio.js").status_code == 200


def edit_elsewhere(path: Path) -> None:
    path.write_text(path.read_text() + "\n# edited elsewhere\n")


def test_accept_refuses_a_change_made_after_the_check(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-008: the digest is compared with the bytes being replaced, inside the write step."""
    sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
    real = editing.edited_job

    def racing(text: str, suffix: str, edit: editing.SelectorEdit) -> object:
        edit_elsewhere(root / "job.toml")  # after the bytes were read and checked
        return real(text, suffix, edit)

    monkeypatch.setattr(editing, "edited_job", racing)
    edit = {"source": None, "field": "venue", "selector": "s::text"}
    reply = post(client, "/api/accept", {"file": "job.toml", "edit": edit, "sha256": sha})
    assert reply.status_code == 409 and reply.json()["error"] == "file_changed"
    text = (root / "job.toml").read_text()
    assert text.endswith("# edited elsewhere\n") and 'venue = "s::text"' not in text
    assert not list(root.glob(".*.tmp"))


@pytest.mark.usefixtures("offline_cli_loop")
def test_apply_refuses_a_change_made_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REC-006: apply compares the expected digest with the bytes it replaces."""
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "job.toml").write_text(API_JOB, encoding="utf-8")
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        post(http, "/api/load", {"file": "job.toml"})
        (tmp_path / "board.html").write_text(board("new-title", wrap=True), encoding="utf-8")
        report = post(http, "/api/repair", {"file": "job.toml", "source": 1}).json()["report"]
        version = report["sources"][0]["comparisons"][0]["candidate_version"]
        body = {"file": "job.toml", "source": 1, "version": version, "reason": "ok"}
        post(http, "/api/promote", body)
        sha = post(http, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        real = recipe_apply._verify

        def racing(*args: object) -> None:
            real(*args)  # type: ignore[arg-type]
            edit_elsewhere(tmp_path / "job.toml")

        monkeypatch.setattr(recipe_apply, "_verify", racing)
        reply = post(http, "/api/apply", {"file": "job.toml", "source": 1, "sha256": sha})
    assert reply.status_code == 409 and reply.json()["error"] == "file_changed"
    assert (tmp_path / "job.toml").read_text().endswith("# edited elsewhere\n")


def test_dataset_values_are_capped_in_pages_and_exports(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DATA-004: long field values are cut with a truncated flag in views and downloads."""
    long_title = "T" * 50
    (root / "page.html").write_text(PAGE.replace("Tango", long_title))
    monkeypatch.setattr(routes_dataset, "MAX_CHARS", 10)
    post(client, "/api/load", {"file": "job.toml"})
    page = post(client, "/api/records", {"file": "job.toml"}).json()
    row = next(r for r in page["records"] if r["fields"]["title"].startswith("T"))
    assert row["truncated"] is True
    assert row["fields"]["title"] == "T" * 10 + "…"
    assert all(len(str(v)) <= 11 for r in page["records"] for v in r["fields"].values())
    exported = post(client, "/api/export", {"file": "job.toml", "format": "json"}).json()
    records = json.loads(exported["content"])["records"]
    assert long_title not in exported["content"]
    cut = next(r for r in records if r["fields"]["title"].startswith("T"))
    assert cut["fields"]["title"] == "T" * 10 + "…" and cut["truncated"] is True
    assert all(len(str(v)) <= 11 for v in cut["key"]["identity"])
    html = post(client, "/api/export", {"file": "job.toml", "format": "html"}).json()["content"]
    assert long_title not in html and "T" * 10 + "…" in html
