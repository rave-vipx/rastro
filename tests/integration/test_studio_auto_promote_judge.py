"""Studio's auto-promote switch never promotes a repair without a semantic judge."""

from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")
from rastro.application.job_loader import load_job
from rastro.domain.jobs import parse_job
from rastro.studio.app import create_app
from rastro.studio.routes_repair import NO_JUDGE, has_judge
from tests.integration.test_job_api import JOB, board, history
from tests.integration.test_job_repair_model import FakeModel
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def _repair_after_drift(root: Path, model: FakeModel | None = None) -> dict:
    """The reviewer's exact input: load, switch auto-promote on, change the page, Fix."""
    (root / "job.toml").write_text(JOB)
    page = root / "board.html"
    page.write_text(board())
    app, token = create_app(root, model=model)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert post(client, "/api/load", {"file": "job.toml"}).status_code == 200
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        switched = post(
            client, "/api/auto-promote", {"file": "job.toml", "sha256": sha, "enabled": True}
        )
        assert switched.status_code == 200
        page.write_text(board("new-title", wrap=True))
        response = post(client, "/api/repair", {"file": "job.toml", "source": 1})
        assert response.status_code == 200, response.text
        payload = response.json()
        health = post(client, "/api/health", {"file": "job.toml"}).json()
    payload["health_alerts"] = health["alerts"]
    return payload


def test_auto_promote_without_a_model_keeps_the_candidate_and_says_why(tmp_path: Path) -> None:
    """No Studio, job or repair-env model means no judge: candidate only, v1 serves."""
    payload = _repair_after_drift(tmp_path)
    (source,) = payload["report"]["sources"]
    assert source["promoted_version"] is None
    assert source["comparisons"], "the repair candidate is still stored for review"
    assert payload["alerts"][0] == NO_JUDGE
    assert NO_JUDGE in payload["health_alerts"]
    repository, key = history(tmp_path / "job.toml")
    assert repository.get(key).recipe.version == 1
    assert load_job(tmp_path / "job.toml").repair.auto_promote is True  # kept for later


@pytest.mark.parametrize(("verdict", "promoted"), [("match", True), ("mismatch", False)])
def test_a_configured_judge_decides_and_can_veto(
    tmp_path: Path, verdict: str, promoted: bool
) -> None:
    """With the Studio model the gates plus the judge decide; a mismatch vetoes."""
    model = FakeModel(verdict)
    payload = _repair_after_drift(tmp_path, model)
    (source,) = payload["report"]["sources"]
    assert (source["promoted_version"] is not None) is promoted
    assert NO_JUDGE not in payload["alerts"]
    assert any(request.response_schema is not None for request in model.requests)


def test_judge_detection_mirrors_repair_model_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Studio model, job [model], or [repair] model = true with RASTRO_MODEL; nothing else."""
    monkeypatch.delenv("RASTRO_MODEL", raising=False)
    plain = parse_job(JOB, format="toml")
    env_job = parse_job(JOB + "\n[repair]\nmodel = true\n", format="toml")
    declared = parse_job(
        JOB + '\n[model]\nprovider = "ollama"\nmodel = "qwen3:4b"\n', format="toml"
    )
    assert has_judge(plain, None) is False
    assert has_judge(plain, FakeModel()) is True
    assert has_judge(declared, None) is True
    assert has_judge(env_job, None) is False  # asks for RASTRO_MODEL, which is unset
    monkeypatch.setenv("RASTRO_MODEL", "ollama:qwen3:4b")
    assert has_judge(env_job, None) is True
    assert has_judge(plain, None) is False  # an environment model alone is not used
