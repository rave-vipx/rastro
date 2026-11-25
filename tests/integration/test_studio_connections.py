"""Settings routes: model, channels, schedule, proxies, robots, per-origin switches."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("httpx2")
from rastro.application import operator_trust
from rastro.domain.model_trust import OperatorTrust
from rastro.studio import routes_connections
from rastro.studio.app import create_app
from rastro.studio.files import digest
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
JOB = """format_version = 1
[dataset]
name = "t"
path = "t.sqlite"
identity = ["title"]
[acquisition]
allowed_hosts = ["example.test"]
[acquisition.egress_profiles]
alpha = { proxy_url = "http://127.0.0.1:9", username_env = "PROXY_USER" }
[[sources]]
kind = "file"
path = "page.html"
url = "https://example.test/x"
[sources.recipe]
version = 1
strategy = "css"
record_selector = "article"
[sources.recipe.fields]
title = "h2::text"
[[fields]]
name = "title"
type = "string"
"""


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    previous = operator_trust.set_process_trust(OperatorTrust())
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text("<article><h2>x</h2></article>", encoding="utf-8")
    for name in ("RASTRO_NOTIFY_SLACK_WEBHOOK_URL", "RASTRO_NOTIFY_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        yield client, tmp_path
    operator_trust.set_process_trust(previous)


def test_every_route_needs_token_and_exact_origin(studio) -> None:
    client, _ = studio
    for path in ("/api/models", "/api/channels", "/api/grants"):
        assert client.get(path, headers={"X-Studio-Token": "no"}).status_code == 403
    for path in ("/api/models", "/api/channels/test", "/api/robots", "/api/grants"):
        assert post(client, path, {}, Origin="http://evil.test").status_code == 403
        assert post(client, path, {}, **{"X-Studio-Token": "no"}).status_code == 403


def test_channels_name_variables_never_values(studio, monkeypatch) -> None:
    client, _ = studio
    monkeypatch.setenv("RASTRO_NOTIFY_WEBHOOK_URL", "https://hooks.example/SECRET-TOKEN")
    data = client.get("/api/channels")
    rows = {r["channel"]: r for r in data.json()["channels"]}
    assert rows["webhook"]["configured"] and not rows["slack"]["configured"]
    assert rows["slack"]["env"] == ["RASTRO_NOTIFY_SLACK_WEBHOOK_URL"]
    assert "SECRET-TOKEN" not in data.text


def test_channel_test_records_last_result_without_secret(studio, monkeypatch) -> None:
    client, _ = studio
    assert post(client, "/api/channels/test", {"channel": "bogus"}).status_code == 400
    refused = post(client, "/api/channels/test", {"channel": "slack"}).json()
    assert refused["sent"] is False and "RASTRO_NOTIFY_SLACK" in refused["reason"]
    last = {r["channel"]: r["last"] for r in client.get("/api/channels").json()["channels"]}
    assert last["slack"]["sent"] is False and last["email"] is None


def test_model_choice_needs_detection_and_consent(studio, monkeypatch) -> None:
    client, _ = studio
    monkeypatch.setattr(routes_connections, "detect_local_models", lambda: ())
    assert client.get("/api/models").json()["detected"] == []
    assert (
        post(client, "/api/models", {"kind": "local", "spec": "command:rm -rf"}).json()["error"]
        == "model_not_detected"
    )
    remote = {"kind": "remote", "model": "m", "base_url": "https://api.example/v1"}
    assert post(client, "/api/models", remote).json()["error"] == "consent_required"
    assert (
        post(client, "/api/models", {**remote, "consent": True, "base_url": "http://x"}).json()[
            "error"
        ]
        == "https_required"
    )
    leak = {**remote, "consent": True, "api_key_env": "HOME"}
    assert post(client, "/api/models", leak).json()["error"] == "env_name_not_allowed"


def test_schedule_words_preview_and_save_keep_the_rest_of_the_job(studio) -> None:
    client, root = studio
    path = root / "job.toml"
    sha = digest(path.read_bytes())
    view = post(client, "/api/schedule/preview", {"file": "job.toml", "words": "every day at 8:00"})
    assert len(view.json()["next"]) == 3 and view.json()["every"] == "1d"
    assert (
        post(
            client, "/api/schedule/preview", {"file": "job.toml", "words": "gibberish"}
        ).status_code
        == 400
    )
    body = {"file": "job.toml", "sha256": sha, "words": "every 6 hours"}
    saved = post(client, "/api/schedule/save", body).json()
    assert saved["ok"] and tomllib.loads(path.read_text())["schedule"] == {"every": "6h"}
    assert "alpha" in path.read_text()
    assert post(client, "/api/schedule/save", body).status_code == 409  # stale digest
    shown = client.get("/api/schedule?file=job.toml&language=es").json()
    assert shown["words"] == "cada 6 horas"


def test_proxies_list_without_credentials_and_test_needs_grant(studio) -> None:
    client, _ = studio
    data = client.get("/api/proxies?file=job.toml").json()
    assert data["proxies"] == [
        {"name": "alpha", "host": "127.0.0.1", "port": 9, "env": ["PROXY_USER"]}
    ]
    assert data["allowed"] is False
    assert (
        post(client, "/api/proxies/test", {"file": "job.toml", "name": "alpha"}).status_code == 403
    )


def test_robots_switch_edits_only_that_key(studio) -> None:
    client, root = studio
    path = root / "job.toml"
    body = {"file": "job.toml", "sha256": digest(path.read_bytes()), "mode": "ignore"}
    assert post(client, "/api/robots", body).json()["robots"] == "ignore"
    assert tomllib.loads(path.read_text())["acquisition"]["robots"] == "ignore"
    assert post(client, "/api/robots", {**body, "mode": "maybe"}).status_code == 400


def test_origin_switches_are_session_grants(studio) -> None:
    client, _ = studio
    assert (
        post(
            client, "/api/grants", {"origin": "operator", "capability": "stealth", "on": True}
        ).status_code
        == 400
    )
    assert (
        post(client, "/api/grants", {"origin": "mcp", "capability": "cdp", "on": True}).status_code
        == 400
    )
    shown = post(
        client, "/api/grants", {"origin": "mcp", "capability": "stealth", "on": True}
    ).json()
    row = {
        c["name"]: c for o in shown["origins"] if o["origin"] == "mcp" for c in o["capabilities"]
    }
    assert row["stealth"]["on"] and not row["stealth"]["locked"]
    assert "stealth" in operator_trust.current_trust().untrusted_capabilities["mcp"]
    off = post(
        client, "/api/grants", {"origin": "mcp", "capability": "stealth", "on": False}
    ).json()
    assert not any(c["on"] for o in off["origins"] for c in o["capabilities"])
    assert json.dumps(off)
