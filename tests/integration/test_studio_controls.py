"""N4/N7/N12: guarded Studio settings edit only the owned job and keep gates."""

import json
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")
from rastro.application.job_loader import load_job
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB, board
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def test_settings_are_guarded_and_change_only_requested_properties(tmp_path: Path) -> None:
    """N4/N12: pagination and auto-promote persist with digest and selector gates."""
    path = tmp_path / "job.toml"
    path.write_text(JOB)
    other = tmp_path / "other.toml"
    other.write_text(JOB)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        opened = post(client, "/api/validate", {"file": "job.toml"}).json()
        assert opened["auto_promote"] is False
        before = load_job(path).model_dump(mode="json")
        data = {
            "file": "job.toml",
            "sha256": opened["sha256"],
            "source": 0,
            "next": "a.next::attr(href)",
            "max_pages": 3,
        }
        saved = post(client, "/api/pagination", data)
        assert saved.status_code == 200, saved.text
        changed = load_job(path).model_dump(mode="json")
        assert changed["sources"][0]["recipe"].pop("pagination") == {
            "next": "a.next::attr(href)",
            "max_pages": 3,
        }
        assert changed == before
        assert post(client, "/api/pagination", data).status_code == 409
        sha = saved.json()["sha256"]
        before_bytes = path.read_bytes()
        for expression in ("a:has(b)::attr(href)", "a + a::attr(href)"):
            assert (
                post(
                    client, "/api/pagination", {**data, "sha256": sha, "next": expression}
                ).status_code
                == 400
            )
            assert path.read_bytes() == before_bytes
        switched = post(
            client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, "enabled": True}
        )
        assert switched.status_code == 200, switched.text
        assert load_job(path).repair.auto_promote is True
        assert other.read_text() == JOB
        assert "allow_stealth" not in path.read_text()


def test_session_stealth_does_not_write_a_job(tmp_path: Path) -> None:
    """N12: the switch starts off and changes runtime trust only."""
    (tmp_path / "job.toml").write_text(JOB)
    app, token = create_app(tmp_path, allow_stealth=False)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert client.get("/api/session").json()["allow_stealth"] is False
        assert post(client, "/api/session", {"allow_stealth": True}).json()["allow_stealth"] is True
        assert (
            post(client, "/api/session", {"allow_stealth": False}).json()["allow_stealth"] is False
        )
        assert post(client, "/api/session", {"allow_stealth": "yes"}).status_code == 400
    assert (tmp_path / "job.toml").read_text() == JOB


def test_completion_progress_contains_only_safe_outcomes(tmp_path: Path) -> None:
    """N5: synchronous runs keep their shape and report real completion without secrets."""
    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "board.html").write_text(board())
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert client.get("/api/progress").json()["state"] == "idle"
        result = post(client, "/api/extract", {"file": "job.toml"}).json()
        assert result["job"]["status"] == "success"
        progress = client.get("/api/progress").json()
        assert progress["state"] == "completed" and progress["outcome"] == "success"
        assert "Synthetic role" not in json.dumps(progress)
        assert result["alerts"] == []


def test_rejected_promote_cannot_override_gates(tmp_path: Path) -> None:
    """N7: Studio refuses overrides and a candidate rejected by real CI never serves."""
    from datetime import UTC, datetime

    import rastro
    from tests.integration.test_job_api import history

    path = tmp_path / "job.toml"
    path.write_text(JOB)
    (tmp_path / "board.html").write_text(board())
    rastro.load_job_file(path)
    repository, key = history(path)
    recipe = load_job(path).sources[0].recipe
    wrong = recipe.model_copy(
        update={"version": 2, "fields": {"job_id": "::attr(data-id)", "title": "main::text"}}
    )
    repository.save_candidate(key, wrong, selected_index=0, at=datetime.now(UTC))
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        data = {"file": "job.toml", "source": 1, "version": 2, "reason": "Checked"}
        override = post(client, "/api/promote", {**data, "override_gates": True})
        assert override.status_code == 400
        assert override.json()["error"] == "gate_override_refused"
        refused = post(client, "/api/promote", data)
        assert refused.status_code == 200
        assert refused.json()["report"]["sources"][0]["promoted_version"] is None
        assert any("Promotion refused" in message for message in refused.json()["alerts"])
        assert repository.get(key).recipe.version == 1
        assert repository.get(key, version=2).metadata.state == "retired"
        assert client.get("/api/progress").json()["outcome"] == "refused"


def test_stealth_trust_reaches_studio_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N12: session trust reaches extract, load, preview and repair without job grants."""
    from rastro.studio import routes_jobs, routes_repair, routes_run

    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "board.html").write_text(board())
    calls = []

    def observe(module, name):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            calls.append((name, kwargs.get("allow_stealth")))
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for module, name in (
        (routes_jobs, "preview_job"),
        (routes_run, "run_job"),
        (routes_run, "load_job_file"),
        (routes_repair, "repair_job_source"),
    ):
        observe(module, name)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        for enabled in (False, True):
            post(client, "/api/session", {"allow_stealth": enabled})
            for endpoint in ("extract", "preview", "load", "repair"):
                response = post(client, f"/api/{endpoint}", {"file": "job.toml", "source": 1})
                assert response.status_code == 200, response.text
            assert all(value is enabled for _, value in calls[-4:])
    assert (tmp_path / "job.toml").read_text() == JOB


def test_live_operation_is_guarded_bounded_and_failure_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N5: polling sees actual running state, competing work is refused, failure is explicit."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from rastro.domain.models import ConfigurationError
    from rastro.studio import routes_run

    started, release = Event(), Event()

    def fail(*args, **kwargs):
        started.set()
        assert release.wait(5)
        raise ConfigurationError("Synthetic failure")

    monkeypatch.setattr(routes_run, "run_job", fail)
    (tmp_path / "job.toml").write_text(JOB)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client, ThreadPoolExecutor() as pool:
        client.headers["X-Studio-Token"] = token
        future = pool.submit(post, client, "/api/extract", {"file": "job.toml"})
        assert started.wait(5)
        try:
            progress = client.get("/api/progress").json()
            assert progress["state"] == "running"
            assert "page_index" not in progress
            assert post(client, "/api/extract", {"file": "job.toml"}).status_code == 409
            assert client.get("/api/progress", headers={"X-Studio-Token": "bad"}).status_code == 403
        finally:
            release.set()
        assert future.result().status_code == 400
        assert client.get("/api/progress").json()["state"] == "failed"
