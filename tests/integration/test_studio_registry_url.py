"""N10, SEC-001: Studio delegates URL acquisition and refuses unavailable fetches."""

from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from tests.integration.test_studio_registry import client as client
from tests.integration.test_studio_registry import publish


@pytest.mark.parametrize(
    "registry",
    [
        "http://example.com/registry",
        "https://user:secret@example.com/registry",
        "https://example.com/?token=secret",
        "https://example.com/#secret",
        "https://",
    ],
)
def test_unsafe_urls_and_remote_publish_refused(client: TestClient, registry: str) -> None:
    response = client.post("/api/registry/search", json={"registry": registry, "query": ""})
    assert response.status_code == 400 and response.json()["error"] == "invalid_registry_url"
    assert "secret" not in response.text
    response = client.post(
        "/api/registry/publish",
        json={"registry": "https://example.com/registry", "bundle": "bundle"},
    )
    assert response.status_code == 400


def test_url_unavailable_is_explicit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from rastro.studio import routes_registry

    def unavailable(name: str) -> None:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(routes_registry, "import_module", unavailable)
    for endpoint, extra in [
        ("search", {"query": ""}),
        ("install", {"file": "job.toml", "spec": "uy-sodre-agenda"}),
    ]:
        response = client.post(
            f"/api/registry/{endpoint}",
            json={"registry": "https://registry.example/catalog/", **extra},
        )
        assert response.status_code == 503
        assert response.json()["ok"] is False


def test_url_delegates_to_acquisition_owner(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from rastro.application.recipe_registry import install_from_registry, search_registry
    from rastro.studio import routes_registry

    publish(client)
    seen = []

    def search(url: str, query: str) -> object:
        seen.append((url, query))
        return search_registry(tmp_path / "registry", query)

    def install(url: str, *args: object, **kwargs: object) -> object:
        seen.append(url)
        return install_from_registry(tmp_path / "registry", *args, **kwargs)

    module = SimpleNamespace(search_registry_url=search, install_from_registry_url=install)
    monkeypatch.setattr(routes_registry, "import_module", lambda name: module)
    url = "https://registry.example/catalog/"
    response = client.post("/api/registry/search", json={"registry": url, "query": "sodre"})
    assert response.status_code == 200 and len(response.json()["entries"]) == 1
    before = (tmp_path / "job.toml").read_bytes()
    response = client.post(
        "/api/registry/install",
        json={
            "registry": url,
            "spec": "uy-sodre-agenda",
            "file": "job.toml",
            "auto_promote": False,
        },
    )
    assert response.status_code == 200 and response.json()["state"] == "candidate"
    assert seen == [(url, "sodre"), url]
    assert (tmp_path / "job.toml").read_bytes() == before


def test_url_fetch_failure_does_not_write_memory(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from rastro.domain.registry import RegistryError
    from rastro.studio import routes_registry

    def refused(*args: object, **kwargs: object) -> None:
        raise RegistryError("Registry fetch failed: destination_denied")

    monkeypatch.setattr(
        routes_registry,
        "import_module",
        lambda name: SimpleNamespace(
            search_registry_url=refused, install_from_registry_url=refused
        ),
    )
    for endpoint, extra in [
        ("search", {"query": ""}),
        ("install", {"file": "job.toml", "spec": "uy-sodre-agenda"}),
    ]:
        response = client.post(
            f"/api/registry/{endpoint}",
            json={"registry": "https://registry.example/catalog/", **extra},
        )
        assert response.status_code == 400 and not response.json()["ok"]
    assert not (tmp_path / "memory.sqlite").exists()
