"""N4/N5/N12: settings safety and readable alerts use offline, synthetic jobs."""

from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB, board
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def test_controls_start_off_and_external_stealth_grant_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N12: HTML defaults off; explicit environment and constructor grants are visible."""
    for granted in (False, True):
        app, token = create_app(tmp_path, allow_stealth=granted)
        with serving(app, "http://127.0.0.1:8765") as client:
            client.headers["X-Studio-Token"] = token
            page = client.get("/").text
            for control in ("auto-promote", "allow-stealth"):
                line = next(line for line in page.splitlines() if f'id="{control}"' in line)
                assert "checked" not in line
            assert client.get("/api/session").json()["allow_stealth"] is granted
    monkeypatch.setenv("RASTRO_ALLOW_STEALTH", "1")
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert client.get("/api/session").json()["allow_stealth"] is True


def test_failed_load_and_health_explain_staleness(tmp_path: Path) -> None:
    """N5: a failure alerts immediately, preserves last success and shows the hook command."""
    (tmp_path / "job.toml").write_text(JOB)
    page = tmp_path / "board.html"
    page.write_text(board())
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        post(client, "/api/load", {"file": "job.toml"})
        page.write_text("<html><body>Temporarily unavailable</body></html>")
        failed = post(client, "/api/load", {"file": "job.toml"}).json()
        assert failed["result"]["status"] == "failed"
        assert any("failed" in message for message in failed["alerts"])
        health = post(client, "/api/health", {"file": "job.toml"}).json()
        assert health["sources"][0]["status"]["last_success"] is not None
        assert health["sources"][0]["status"]["stale"] is True
        assert any("stale" in message for message in health["alerts"])
        assert (
            health["on_failure_command"]
            == "rastro job load job.toml --drift --on-failure 'python notify.py'"
        )


def test_settings_refuse_concurrent_write_at_atomic_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N4: a concurrent edit after validation survives unchanged and returns conflict."""
    from rastro.studio import files

    path = tmp_path / "job.toml"
    path.write_text(JOB)
    original = files.write_atomic

    def racing_write(target, raw, *, expected_digest):
        target.write_text(JOB + "\n# concurrent change\n")
        original(target, raw, expected_digest=expected_digest)

    monkeypatch.setattr(files, "write_atomic", racing_write)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        saved = post(
            client,
            "/api/pagination",
            {
                "file": "job.toml",
                "sha256": sha,
                "source": 0,
                "next": "a::attr(href)",
                "max_pages": 2,
            },
        )
        assert saved.status_code == 409
    assert path.read_text() == JOB + "\n# concurrent change\n"


@pytest.mark.parametrize(
    "payload, fragment",
    [
        ({"open_drift": [1]}, "site changed"),
        ({"sources": [{"versions": [{"state": "quarantined"}]}]}, "quarantined"),
        (
            {"sources": [{"comparisons": [{"evaluation": {"decision": "reject"}}]}]},
            "Promotion refused",
        ),
        ({"sources": [{"pagination_stop": "max_pages"}]}, "cap"),
        ({"sources": [{"issues": [{"code": "scroll_step_cap"}]}]}, "cap"),
    ],
)
def test_alerts_translate_real_outcome_fields(payload, fragment) -> None:
    """N5: drift, quarantine, refusal and pagination/scroll caps get human-readable copy."""
    from rastro.studio.operations import alerts

    assert any(fragment in message for message in alerts(payload))
