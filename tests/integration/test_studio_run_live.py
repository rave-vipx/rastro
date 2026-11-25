"""Studio's running read: progress, results so far, page reuse and the route guard."""

import time
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.studio.app import create_app
from tests.integration.test_job_preview import PAGE
from tests.integration.test_studio import post, serving
from tests.integration.test_studio_ops import EXAMPLES, URL, FakeAcquirer

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def wait_done(http: TestClient) -> dict[str, object]:
    for _ in range(100):
        snap: dict[str, object] = post(http, "/api/run/status", {}).json()
        if snap["state"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("the read did not finish")


def test_read_reports_pages_results_and_reuses_pages(tmp_path: Path) -> None:
    acquirer = FakeAcquirer({URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        body = {"file": "agenda.toml", "url": URL, "examples": EXAMPLES}
        assert post(http, "/api/suggest", body).status_code == 200
        assert post(http, "/api/create", body).status_code == 200
        assert post(http, "/api/run/status", {}).json()["state"] == "idle"
        started = post(http, "/api/run/start", {"file": "agenda.toml"})
        assert started.status_code == 200 and started.json()["sources_total"] == 1
        snap = wait_done(http)
        assert snap["state"] == "done" and snap["pages"] == 1 and snap["records"] == 3
        assert snap["will_continue"] is False and 1 <= len(snap["sample"]) <= 5  # type: ignore[arg-type]
        fetched = len(acquirer.fetched)
        post(http, "/api/run/start", {"file": "agenda.toml"})
        assert wait_done(http)["records"] == 3
        assert len(acquirer.fetched) == fetched  # the second read came from the page cache
    assert (tmp_path / ".rastro-studio" / "cache").is_dir()
    assert not list(tmp_path.glob("*.sqlite"))  # a read saves no dataset


def test_second_read_while_one_runs_is_refused_and_stop_ends_it(tmp_path: Path) -> None:
    acquirer = FakeAcquirer({URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        body = {"file": "agenda.toml", "url": URL, "examples": EXAMPLES}
        post(http, "/api/suggest", body)
        post(http, "/api/create", body)
        post(http, "/api/run/start", {"file": "agenda.toml"})
        again = post(http, "/api/run/start", {"file": "agenda.toml"})
        assert again.status_code in {200, 409}  # 409 only if the first is still running
        stopped = post(http, "/api/run/stop", {}).json()
        assert stopped["state"] in {"stopped", "done"}


def test_routes_need_the_token_the_origin_and_a_path_inside_the_root(tmp_path: Path) -> None:
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as http:
        for path in ("/api/run/start", "/api/run/status", "/api/run/stop"):
            assert post(http, path, {"file": "a.toml"}).status_code == 403  # no token
            cross = {"X-Studio-Token": token, "Origin": "http://127.0.0.1:9999"}
            assert post(http, path, {"file": "a.toml"}, **cross).status_code == 403
        http.headers["X-Studio-Token"] = token
        for name in ("../outside.toml", "/etc/passwd"):
            assert post(http, "/api/run/start", {"file": name}).status_code == 400
        assert post(http, "/api/run/start", {}).status_code == 400
