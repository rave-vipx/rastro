"""SQLite-only views refuse PostgreSQL-backed jobs, never show empty data (DATA-003, SEC-011).

A synthetic job whose [dataset] backend is postgres; no PostgreSQL server or driver is needed
because the views must refuse before they touch any dataset.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")
pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from rastro.adapters.mcp_server import build_server
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB, board
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
MESSAGE = "Studio/MCP dataset views support SQLite only; query PostgreSQL with SQL"
POSTGRES = 'backend = "postgres"\ndsn_env = "RASTRO_PG_DSN"\n'


@pytest.fixture
def root(tmp_path: Path) -> Path:
    postgres = JOB.replace('path = "jobs.sqlite"\n', POSTGRES)
    assert postgres != JOB
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "job.toml").write_text(postgres, encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


def refused(response: Any) -> None:
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["ok"] is False and MESSAGE in body["message"]


def test_studio_records_refuses_postgres(client: TestClient) -> None:
    """DATA-003: /api/records is an explicit error, not "no records yet"."""
    refused(post(client, "/api/records", {"file": "job.toml"}))


def test_studio_export_refuses_postgres(client: TestClient) -> None:
    """DATA-003: /api/export is an explicit error for a PostgreSQL job."""
    refused(post(client, "/api/export", {"file": "job.toml", "format": "json"}))


def test_studio_health_refuses_postgres(client: TestClient) -> None:
    """DATA-003: /api/health must not report `status: None` as if nothing were stored."""
    refused(post(client, "/api/health", {"file": "job.toml"}))


@pytest.mark.asyncio
async def test_mcp_drift_status_refuses_postgres(root: Path) -> None:
    """DATA-003: drift_status returns the tool error shape, not empty sources."""
    built = build_server(root)
    async with create_connected_server_and_client_session(built._mcp_server) as client:
        result = await client.call_tool("drift_status", {"path": "job.toml"})
    error = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert result.isError and MESSAGE in error["message"] and "sources" not in error
