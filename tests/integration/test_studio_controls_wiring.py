"""N4/N10/N12: registry wiring, JSON jobs, invalid settings, stealth refusal, CLI grant."""

import json
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")
from rastro.application.job_loader import load_job
from rastro.studio import routes_jobs
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
STATIC = Path(routes_jobs.__file__).parent / "static"
STEALTH_JOB = """format_version = 1
[acquisition]
render = "stealth"
allowed_hosts = ["example.test"]
[recipe]
strategy = "css"
record_selector = "article"
fields = { title = "h2::text" }
[[sources]]
url = "https://example.test/list"
[[fields]]
name = "title"
type = "string"
"""


def _client(root: Path, **options: object):
    app, token = create_app(root, **options)
    client = serving(app, "http://127.0.0.1:8765")
    return client, token


def test_registry_routes_assets_and_init_are_wired(tmp_path: Path) -> None:
    """N10: create_app serves the registry API and both new modules; studio.js starts them."""
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        for name in ("registry.js", "controls.js"):
            assert client.get(f"/static/{name}").status_code == 200
        for path in ("/api/registry/search", "/api/registry/install", "/api/registry/publish"):
            refused = post(client, path, {})
            assert refused.status_code == 400, path  # routed and guarded, not 404/405
            assert post(client, path, {}, **{"X-Studio-Token": "wrong"}).status_code == 403
    entry = (STATIC / "studio.js").read_text()
    assert 'import { initRegistry } from "./registry.js";' in entry
    assert "initRegistry();" in entry and "await initControls();" in entry
    assert "Override failed gates" not in (STATIC / "operate.js").read_text()


def test_json_job_pagination_and_auto_promote_change_only_that_property(tmp_path: Path) -> None:
    """N4/N12: the JSON writer keeps every other value; reparsed job differs only there."""
    import tomllib

    path = tmp_path / "job.json"
    path.write_text(json.dumps(tomllib.loads(JOB)))
    before = load_job(path).model_dump(mode="json")
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        opened = post(client, "/api/validate", {"file": "job.json"}).json()
        assert opened["recipes"] == [{"source": 0, "pagination": None}]
        data = {"file": "job.json", "sha256": opened["sha256"], "source": 0}
        saved = post(
            client, "/api/pagination", {**data, "next": "a.more::attr(href)", "max_pages": 4}
        )
        assert saved.status_code == 200, saved.text
        switched = post(
            client, "/api/auto-promote", {**data, "sha256": saved.json()["sha256"], "enabled": True}
        )
        assert switched.status_code == 200, switched.text
    after = load_job(path).model_dump(mode="json")
    pagination = after["sources"][0]["recipe"].pop("pagination")
    assert pagination == {"next": "a.more::attr(href)", "max_pages": 4}
    assert after.pop("repair")["auto_promote"] is True
    before.pop("repair", None)
    assert after == before


@pytest.mark.parametrize(
    "extra",
    [
        {"source": 5},
        {"source": True},
        {"source": None},  # JOB has no job-level recipe, only a per-source one
        {"max_pages": 51},
        {"max_pages": 0},
        {"max_pages": True},
        {"next": ""},
        {"next": "document:a::attr(href)"},
        {"next": "a ~ a::attr(href)"},
        {"sha256": "stale"},
        {"file": "../job.toml"},
    ],
)
def test_invalid_pagination_writes_nothing(tmp_path: Path, extra: dict[str, object]) -> None:
    """N4: bad index, cap, expression, digest or path is refused and the bytes stay."""
    path = tmp_path / "job.toml"
    path.write_text(JOB)
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        data = {"file": "job.toml", "sha256": sha, "source": 0, "next": "a::attr(href)"}
        response = post(client, "/api/pagination", {**data, "max_pages": 2, **extra})
        assert response.status_code in (400, 409), response.text
    assert path.read_text() == JOB


def test_auto_promote_toml_keeps_comments_and_refuses_bad_input(tmp_path: Path) -> None:
    """N12: off/on round trip edits one key; non-boolean or missing values are refused."""
    path = tmp_path / "job.toml"
    path.write_bytes((JOB + "\n[repair]\nmin_records = 1  # keep\n").encode())
    client_cm, token = _client(tmp_path, allow_stealth=True)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        for bad in ({}, {"enabled": "true"}, {"enabled": 1}):
            response = post(client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, **bad})
            assert response.status_code == 400
        for enabled in (True, False):
            sha = post(
                client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, "enabled": enabled}
            ).json()["sha256"]
            assert load_job(path).repair.auto_promote is enabled
    text = path.read_text()
    assert text.startswith(JOB) and "min_records = 1  # keep" in text
    assert "stealth" not in text  # the session grant was on; the job never records it


def test_stealth_job_without_session_grant_is_refused_not_downgraded(tmp_path: Path) -> None:
    """N12: engine stealth without the grant fails explicitly; no plain-browser fallback."""
    (tmp_path / "job.toml").write_text(STEALTH_JOB)
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        assert client.get("/api/session").json() == {
            "allow_stealth": False,
            "locked_by_operator": False,
        }
        refused = post(client, "/api/extract", {"file": "job.toml"})
        assert refused.status_code == 400
        assert "allow-stealth" in refused.json()["message"]
        assert client.get("/api/progress").json()["outcome"] == "failed"
        health = post(client, "/api/health", {"file": "job.toml"}).json()
        assert any("stopped with an error" in message for message in health["alerts"])
    assert (tmp_path / "job.toml").read_text() == STEALTH_JOB


def test_cli_passes_the_stealth_grant_to_create_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N12: `rastro studio --allow-stealth` reaches create_app beside the trust options."""
    import uvicorn

    from rastro.cli import main
    from rastro.studio import app as studio_app

    seen: dict[str, object] = {}
    real = studio_app.create_app

    def capture(root, **options):
        seen.update(options)
        return real(root, **options)

    monkeypatch.setattr(studio_app, "create_app", capture)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.delenv("RASTRO_MODEL", raising=False)
    for argv, expected in ((["studio"], False), (["studio", "--allow-stealth"], True)):
        assert main([*argv, "--root", str(tmp_path)]) == 0
        assert seen["allow_stealth"] is expected
        assert seen["allow_egress"] is False


def test_unusual_repair_layout_is_refused_by_the_reparse_check(tmp_path: Path) -> None:
    """N12: a dotted ``repair.*`` key the line editor cannot place safely is never written."""
    path = tmp_path / "job.toml"
    original = JOB.replace("format_version = 1\n", "format_version = 1\nrepair.min_records = 1\n")
    path.write_text(original)
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        refused = post(
            client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, "enabled": True}
        )
        assert refused.status_code == 400
        assert "edit the job by hand" in refused.json()["message"]
    assert path.read_text() == original


@pytest.mark.parametrize("repair", ["", "\n[repair]\nmin_records = 1  # keep\n"])
def test_crlf_job_settings_preserve_newlines_and_comments(tmp_path: Path, repair: str) -> None:
    """N4/N12: Windows jobs keep CRLF and comments across both settings writers."""
    path = tmp_path / "job.toml"
    original = "# keep job comment\n" + JOB + repair
    path.write_bytes(original.replace("\n", "\r\n").encode())
    client_cm, token = _client(tmp_path)
    with client_cm as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        for enabled in (True, False):
            saved = post(
                client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, "enabled": enabled}
            )
            assert saved.status_code == 200, saved.text
            sha = saved.json()["sha256"]
            assert load_job(path).repair.auto_promote is enabled
            raw = path.read_bytes()
            assert b"\n" not in raw.replace(b"\r\n", b"")
            assert b"# keep job comment\r\n" in raw
            if repair:
                assert b"min_records = 1  # keep\r\n" in raw
        saved = post(
            client,
            "/api/pagination",
            {
                "file": "job.toml",
                "sha256": sha,
                "source": 0,
                "next": "a::attr(href)",
                "max_pages": 3,
            },
        )
        assert saved.status_code == 200, saved.text
    assert load_job(path).sources[0].recipe.pagination.max_pages == 3
    raw = path.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert b"# keep job comment\r\n" in raw
    if repair:
        assert b"min_records = 1  # keep\r\n" in raw
