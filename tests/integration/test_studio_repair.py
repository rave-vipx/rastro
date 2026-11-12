"""Studio health and repair: propose, promote with reason, write to the job file.

(P-008, REPAIR-002-005, REC-006, QUAL-006) Synthetic job cards from the public job API tests.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.job_loader import load_job
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB, board
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
FILE = {"file": "job.toml"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    app, token = create_app(root)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


def broken(client: TestClient, root: Path) -> None:
    assert post(client, "/api/load", FILE).json()["result"]["job"]["status"] == "success"
    (root / "board.html").write_text(board("new-title", wrap=True), encoding="utf-8")


def proposed(client: TestClient, root: Path) -> dict[str, object]:
    broken(client, root)
    reply = post(client, "/api/repair", {**FILE, "source": 1})
    assert reply.status_code == 200, reply.text
    report: dict[str, object] = reply.json()["report"]
    return report


def test_health_before_any_run_and_after_load(client: TestClient, root: Path) -> None:
    """P-008: health shows origin and version; stale status appears once a dataset exists."""
    fresh = post(client, "/api/health", FILE).json()
    (source,) = fresh["sources"]
    assert source["recipe_origin"] == "job" and source["recipe_version"] == 1
    assert source["versions"] == [] and source["status"] is None
    assert not (root / "recipes.sqlite").exists() and not (root / "jobs.sqlite").exists()
    broken(client, root)
    post(client, "/api/load", FILE)
    (later,) = post(client, "/api/health", FILE).json()["sources"]
    assert later["status"]["stale"] is True and later["versions"][0]["state"] == "active"


def test_repair_shows_selector_diff_and_gates(client: TestClient, root: Path) -> None:
    """REPAIR-002-005: the candidate diff names old and new selectors and every gate result."""
    report = proposed(client, root)
    (source,) = report["sources"]  # type: ignore[misc]
    comparison = source["comparisons"][0]
    title = next(row for row in comparison["diff"]["fields"] if row["field"] == "title")
    assert title["old"] == "h2.title::text" and title["changed"] and title["new"]
    evaluation = comparison["evaluation"]
    assert evaluation["decision"] in {"promote", "quarantine"}
    assert all("passed" in check for check in evaluation["regressions"])
    assert load_job(root / "job.toml").sources[0].recipe.fields["title"] == "h2.title::text"  # type: ignore[union-attr]


def test_promote_then_apply_writes_the_job_file(client: TestClient, root: Path) -> None:
    """REC-006: promote with a reason, then write the serving recipe with a digest check."""
    report = proposed(client, root)
    version = report["sources"][0]["comparisons"][0]["candidate_version"]  # type: ignore[index]
    body = {**FILE, "source": 1, "version": version, "reason": "Checked in Studio ✅"}
    promoted = post(client, "/api/promote", body).json()["report"]
    assert promoted["sources"][0]["promoted_version"] == version
    stale = post(client, "/api/apply", {**FILE, "source": 1, "sha256": "0" * 64})
    assert stale.status_code == 409
    sha = post(client, "/api/validate", FILE).json()["sha256"]
    applied = post(client, "/api/apply", {**FILE, "source": 1, "sha256": sha}).json()
    assert applied["changed"] is True and applied["recipe_version"] == version
    assert load_job(root / "job.toml").sources[0].recipe.version == version  # type: ignore[union-attr]
    assert (root / "job.toml").read_text().startswith("# Synthetic job board")


@pytest.mark.parametrize(
    "extra",
    [
        {"reason": ""},
        {"reason": "   "},
        {"reason": "bad\x1b[31m"},
        {"reason": "bidi‮"},
        {"reason": "ok", "override_gates": "yes"},
        {"reason": "ok", "version": 0},
        {"reason": "ok", "source": True},
    ],
)
def test_promote_rejects_bad_input(client: TestClient, extra: dict[str, object]) -> None:
    body = {**FILE, "source": 1, "version": 2, **extra}
    assert post(client, "/api/promote", body).status_code == 400


def test_apply_and_repair_refuse_escapes_and_symlinks(client: TestClient, root: Path) -> None:
    (root / "link.toml").symlink_to(root / "job.toml")
    for name in ("../job.toml", "link.toml", "/etc/x.toml"):
        assert (
            post(client, "/api/apply", {"file": name, "source": 1, "sha256": "x"}).status_code
            == 400
        )
        assert post(client, "/api/repair", {"file": name, "source": 1}).status_code == 400
        assert post(client, "/api/health", {"file": name}).status_code == 400
