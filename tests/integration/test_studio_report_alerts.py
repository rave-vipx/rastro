"""The overview Alerts view shows what the latest stored run report says about repair."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.run_report import report_for_run
from rastro.studio import run_log
from rastro.studio.app import create_app
from tests.fixtures.studio_demo.build import build_demo
from tests.integration.test_studio import post, serving
from tests.unit.test_repair_report_consumers import quarantined, result, wrap

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return build_demo(tmp_path)


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, BASE) as http:
        http.headers["X-Studio-Token"] = token
        yield http


def test_logged_report_reaches_the_alerts_view(root: Path, client: TestClient) -> None:
    before = post(client, "/api/alerts", {"file": "agenda.toml"}).json()["alerts"]
    assert not any(a["code"] == "competing_candidates" for a in before)
    report = report_for_run("agenda.toml", wrap(result(quarantined())))
    run = run_log.LoggedRun(at=datetime.now(UTC), report=report)
    run_log.append_run(root, root / "agenda.toml", run)
    after = post(client, "/api/alerts", {"file": "agenda.toml"}).json()["alerts"]
    assert any(a["kind"] == "quarantine" and a["code"] == "competing_candidates" for a in after)


def test_a_log_without_a_report_still_reads() -> None:
    assert run_log.LoggedRun(at=datetime.now(UTC)).report is None
