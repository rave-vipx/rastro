"""N10, N12, HUB-002..006: guarded registry operations never promote or grant trust."""

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.applications import Starlette
from starlette.testclient import TestClient

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.active_recipes import source_key
from rastro.application.job_loader import load_job
from rastro.studio.routes_registry import routes
from rastro.studio.web import Studio
from tests.integration.test_studio import serving
from tests.unit.test_recipe_registry import _write_sample_bundle

JOB = """format_version = 1
recipe_memory = "memory.sqlite"
[repair]
auto_promote = true
[recipe]
strategy = "css"
record_selector = "article.item"
fields = { title = "h2::text" }
[[sources]]
path = "page.html"
url = "https://sodre.gub.uy/espectaculos/"
[[fields]]
name = "title"
type = "string"
min_length = 1
"""


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


def publish(client: TestClient) -> None:
    response = client.post(
        "/api/registry/publish", json={"bundle": "bundle", "registry": "registry"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"]


def test_publish_search_install_candidate(client: TestClient, tmp_path: Path) -> None:
    publish(client)
    response = client.post("/api/registry/search", json={"registry": "registry", "query": "sodre"})
    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["id"] == "uy-sodre-agenda" and entry["trust"]["has_sample"]
    before = (tmp_path / "job.toml").read_bytes()
    payload = {"registry": "registry", "spec": "uy-sodre-agenda@1.0.0", "file": "job.toml"}
    installed = client.post("/api/registry/install", json=payload)
    assert installed.status_code == 200, installed.text
    result = installed.json()
    assert result["state"] == "candidate" and result["promoted"] is False
    assert result["trust"]["replay_verified"]
    assert (tmp_path / "job.toml").read_bytes() == before
    repository = SQLiteRecipeRepository(tmp_path / "memory.sqlite")
    job = load_job(tmp_path / "job.toml")
    key = source_key(job, job.sources[0])
    history = repository.history(key)
    assert history.versions
    assert all(version.state == "candidate" for version in history.versions)
    again = client.post("/api/registry/install", json=payload).json()
    assert again["reused"] and again["state"] == "candidate"


@pytest.mark.parametrize("endpoint", ["search", "install", "publish"])
def test_guard_before_input(client: TestClient, endpoint: str) -> None:
    path = f"/api/registry/{endpoint}"
    assert client.post(path, json={}, headers={"X-Studio-Token": "wrong"}).status_code == 403
    assert client.post(path, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(path, content="[]").status_code == 400
    assert client.post(path, content="{").status_code == 400
    assert client.post(path, json={}).status_code == 400


@pytest.mark.parametrize(
    "registry", ["../escape", "/tmp/escape", "x/../../escape", "x\\escape", ""]
)
def test_refuse_registry_escape(client: TestClient, registry: str) -> None:
    for endpoint, extra in [
        ("search", {"query": ""}),
        ("publish", {"bundle": "bundle"}),
        ("install", {"file": "job.toml", "spec": "uy-sodre-agenda"}),
    ]:
        response = client.post(f"/api/registry/{endpoint}", json={"registry": registry, **extra})
        assert response.status_code == 400
        assert response.json()["ok"] is False


@pytest.mark.parametrize("target", ["registry", "bundle", "memory.sqlite", "memory.sqlite-wal"])
def test_refuse_symlinks(client: TestClient, tmp_path: Path, target: str) -> None:
    publish(client)
    path = tmp_path / target
    moved = tmp_path / f"real-{target}"
    if path.exists():
        path.rename(moved)
    elif target == "memory.sqlite-wal":
        moved.write_bytes(b"keep")
    else:
        moved.write_bytes(b"")
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    endpoint = "publish" if target == "bundle" else "install"
    response = client.post(
        f"/api/registry/{endpoint}",
        json={
            "registry": "registry",
            "bundle": "bundle",
            "file": "job.toml",
            "spec": "uy-sodre-agenda",
        },
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_private_publish_and_corrupt_install_fail(client: TestClient, tmp_path: Path) -> None:
    _write_sample_bundle(tmp_path / "private", tmp_path, public=False)
    refused = client.post(
        "/api/registry/publish", json={"bundle": "private", "registry": "registry"}
    )
    assert refused.status_code == 400
    assert not (tmp_path / "registry").exists()
    publish(client)
    package = tmp_path / "registry/recipes/uy-sodre-agenda/1.0.0"
    (package / "sample.html").write_text("tampered")
    refused = client.post(
        "/api/registry/install",
        json={"registry": "registry", "spec": "uy-sodre-agenda", "file": "job.toml"},
    )
    assert refused.status_code == 400 and not refused.json()["ok"]
    assert not (tmp_path / "memory.sqlite").exists()


def test_missing_memory_and_bad_inputs(client: TestClient, tmp_path: Path) -> None:
    publish(client)
    (tmp_path / "job.toml").write_text(JOB.replace('recipe_memory = "memory.sqlite"\n', ""))
    response = client.post(
        "/api/registry/install",
        json={"registry": "registry", "spec": "uy-sodre-agenda", "file": "job.toml"},
    )
    assert response.status_code == 400 and response.json()["error"] == "recipe_memory_required"
    (tmp_path / "registry/index.json").write_text("invalid")
    response = client.post("/api/registry/search", json={"registry": "registry", "query": ""})
    assert response.status_code == 400 and response.json()["ok"] is False
    assert client.post("/api/registry/search", json={"registry": 1, "query": ""}).status_code == 400


def test_publish_never_runs_commands(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Registry publish must not run git or another process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    publish(client)


def test_bundle_file_and_nested_store_symlinks(client: TestClient, tmp_path: Path) -> None:
    sample = tmp_path / "bundle/sample.html"
    real = tmp_path / "real-sample.html"
    sample.rename(real)
    sample.symlink_to(real)
    response = client.post(
        "/api/registry/publish", json={"registry": "registry", "bundle": "bundle"}
    )
    assert response.status_code == 400 and not (tmp_path / "registry").exists()
    sample.unlink()
    real.rename(sample)
    publish(client)
    (tmp_path / "store").mkdir()
    (tmp_path / "linked").symlink_to(tmp_path / "store", target_is_directory=True)
    (tmp_path / "job.toml").write_text(JOB.replace('"memory.sqlite"', '"linked/memory.sqlite"'))
    response = client.post(
        "/api/registry/install",
        json={"registry": "registry", "spec": "uy-sodre-agenda", "file": "job.toml"},
    )
    assert response.status_code == 400 and not (tmp_path / "store/memory.sqlite").exists()


def test_bad_package_and_missing_bundle_never_succeed(client: TestClient, tmp_path: Path) -> None:
    publish(client)
    for spec in ("../escape", "missing-package", "uy-sodre-agenda@9.0.0"):
        response = client.post(
            "/api/registry/install", json={"registry": "registry", "spec": spec, "file": "job.toml"}
        )
        assert response.status_code == 400 and not response.json()["ok"]
    assert not (tmp_path / "memory.sqlite").exists()
    response = client.post(
        "/api/registry/publish", json={"registry": "new-registry", "bundle": "missing"}
    )
    assert response.status_code == 400 and not (tmp_path / "new-registry").exists()
