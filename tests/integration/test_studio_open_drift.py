"""Studio never shows a healthy load or source while drift is open (DRIFT-005, P-008)."""

from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from rastro.studio.app import create_app
from tests.integration.test_open_drift_load import JOB, page
from tests.integration.test_studio import post, serving


def test_load_and_health_report_open_drift(tmp_path: Path) -> None:
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(5), encoding="utf-8")
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        first = post(http, "/api/load", {"file": "job.toml", "drift": True}).json()
        assert first["result"]["status"] == "success"
        (tmp_path / "page.html").write_text(page(1), encoding="utf-8")
        second = post(http, "/api/load", {"file": "job.toml", "drift": True}).json()
        assert second["result"]["status"] == "degraded"
        health = post(http, "/api/health", {"file": "job.toml"}).json()
    (source,) = health["sources"]
    assert source["status"]["stale"] is False
    assert source["open_drift"]["risk"] == "elevated"
