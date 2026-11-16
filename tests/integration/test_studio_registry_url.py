"""N10, SEC-001/005: real registry fetches honor grants; installs stay candidates."""

from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application import registry_fetch
from rastro.studio.web import Studio
from tests.integration.test_studio_registry import client as client
from tests.integration.test_studio_registry import publish
from tests.integration.test_studio_registry import studio as studio
from tests.unit.test_registry_fetch import Resolver, Site

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
BASE = "https://registry.example/catalog/"


@pytest.fixture
def site(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Site:
    publish(client)
    site = Site(tmp_path / "registry")
    # Only replace the I/O ports: every fetch, policy check and install gate is real.
    for name in ("search_registry_url", "install_from_registry_url"):
        monkeypatch.setattr(
            registry_fetch,
            name,
            partial(getattr(registry_fetch, name), transport=site, resolver=Resolver()),
        )
    return site


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


def test_url_search_and_install_real_candidate(
    client: TestClient,
    tmp_path: Path,
    site: Site,
) -> None:
    response = client.post("/api/registry/search", json={"registry": BASE, "query": "sodre"})
    assert response.status_code == 200, response.text
    assert len(response.json()["entries"]) == 1
    before = (tmp_path / "job.toml").read_bytes()
    response = client.post(
        "/api/registry/install",
        json={"registry": BASE, "spec": "uy-sodre-agenda", "file": "job.toml"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "candidate"
    assert response.json()["promoted"] is False
    assert response.json()["trust"]["replay_verified"] is True
    assert (tmp_path / "job.toml").read_bytes() == before
    assert len([url for url in site.requests if url.endswith("index.json")]) == 2
    assert any(url.endswith("sample.html") for url in site.requests)


@pytest.mark.parametrize("grant", ["port", "private", "robots"])
def test_registry_url_requires_operator_grants(
    client: TestClient,
    studio: Studio,
    site: Site,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    grant: str,
) -> None:
    """N10, N12, SEC-001/005: request data cannot grant network permissions."""
    url = BASE
    grants = {}
    if grant == "port":
        url = BASE.replace("registry.example", "registry.example:8443")
        grants = {"allow_ports": [8443]}
    elif grant == "private":
        grants = {"allow_private_network": True}
        for name in ("search_registry_url", "install_from_registry_url"):
            monkeypatch.setattr(
                registry_fetch,
                name,
                partial(
                    getattr(registry_fetch, name), transport=site, resolver=Resolver("10.0.0.5")
                ),
            )
    else:
        site.robots = b"User-agent: *\nDisallow: /\n"
        grants = {"ignore_robots": ["registry.example"]}
    before = (tmp_path / "job.toml").read_bytes()
    for endpoint, extra in [
        ("search", {"query": "sodre"}),
        ("install", {"file": "job.toml", "spec": "uy-sodre-agenda"}),
    ]:
        payload = {"registry": url, **extra, **grants}
        refused = client.post(f"/api/registry/{endpoint}", json=payload)
        assert refused.status_code == 400, refused.text
    assert not (tmp_path / "memory.sqlite").exists()
    studio.trust.update(grants)
    for endpoint, extra in [
        ("search", {"query": "sodre"}),
        ("install", {"file": "job.toml", "spec": "uy-sodre-agenda"}),
    ]:
        allowed = client.post(f"/api/registry/{endpoint}", json={"registry": url, **extra})
        assert allowed.status_code == 200, allowed.text
        if endpoint == "install":
            assert allowed.json()["state"] == "candidate"
    assert (tmp_path / "job.toml").read_bytes() == before


def test_bad_digest_does_not_write_memory(
    client: TestClient,
    tmp_path: Path,
    site: Site,
) -> None:
    (site.root / "recipes/uy-sodre-agenda/1.0.0/sample.html").write_text("tampered")
    response = client.post(
        "/api/registry/install",
        json={"registry": BASE, "spec": "uy-sodre-agenda", "file": "job.toml"},
    )
    assert response.status_code == 400 and not response.json()["ok"]
    assert not (tmp_path / "memory.sqlite").exists()
