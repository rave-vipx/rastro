"""Traffic light on the project and source rows, and the repair routes' guards."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.studio.app import create_app
from tests.fixtures.studio_demo.build import build_demo
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
BASE = "http://127.0.0.1:8765"
LIGHTS = {"green", "amber", "red", "grey"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app, token = create_app(build_demo(tmp_path))
    with serving(app, BASE) as http:
        http.headers["X-Studio-Token"] = token
        yield http


def test_rows_carry_a_light_that_matches_their_status(client: TestClient) -> None:
    for row in client.get("/api/projects").json()["projects"]:
        assert row["light"] in LIGHTS
    rows = post(client, "/api/sources", {"file": "agenda.toml"}).json()["sources"]
    assert rows and all(r["light"] in LIGHTS and "say" in r for r in rows)
    for row in rows:
        if row["status"] in {"failed", "stale"}:
            assert row["light"] == "red"
        if row["status"] == "never":
            assert row["light"] == "grey"


@pytest.mark.parametrize("path", ["/api/repair", "/api/promote", "/api/apply"])
def test_repair_routes_need_token_origin_and_a_contained_path(
    client: TestClient, path: str
) -> None:
    body = {"file": "agenda.toml", "source": 1, "version": 1, "reason": "x", "sha256": "0" * 64}
    token = client.headers.pop("X-Studio-Token")
    assert post(client, path, body).status_code == 403
    client.headers["X-Studio-Token"] = token
    for name in ("../agenda.toml", "/etc/passwd.toml", "x.sqlite"):
        refused = post(client, path, {**body, "file": name})
        assert refused.status_code == 400 and refused.json()["ok"] is False
    wrong = client.post(path, json=body, headers={"Origin": "http://evil.example"})
    assert wrong.status_code == 403
