"""`rastro health`: one command an operator can alert on (DATA-004, DRIFT-005, REPAIR-005)."""

import json
from pathlib import Path

import pytest

import rastro
from rastro.cli import main
from rastro.domain.health import job_health_json_schema
from tests.integration.test_open_drift_load import JOB, page


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(5), encoding="utf-8")
    return tmp_path / "job.toml"


def health(capsys, job):
    code = main(["health", str(job), "--output", "json"])
    return code, json.loads(capsys.readouterr().out)


def test_health_moves_from_never_loaded_to_healthy_to_open_drift_and_stale(job, capsys):
    code, report = health(capsys, job)
    assert (code, report["healthy"]) == (4, False)
    assert report["sources"][0]["attention"] == ["never_loaded"]
    assert not (job.parent / "data.sqlite").exists()

    assert main(["job", "load", str(job), "--drift"]) == 0
    capsys.readouterr()
    code, report = health(capsys, job)
    assert (code, report["healthy"], report["sources"][0]["attention"]) == (0, True, [])
    assert report["sources"][0]["last_success"] is not None

    (job.parent / "page.html").write_text(page(1), encoding="utf-8")
    assert main(["job", "load", str(job), "--drift"]) == 4
    (job.parent / "page.html").write_text("<html><body></body></html>", encoding="utf-8")
    assert main(["job", "load", str(job)]) == 1
    capsys.readouterr()
    code, report = health(capsys, job)
    (source,) = report["sources"]
    assert code == 4 and source["attention"] == ["stale", "open_drift"]
    assert (source["reason"], source["open_drift"]["risk"]) == ("missing_data", "elevated")
    assert rastro.job_health(job).exit_code == 4

    assert main(["health", str(job)]) == 4
    table = capsys.readouterr().out
    assert "needs attention" in table and "stale,open_drift" in table


def test_quarantined_candidates_need_attention(job, capsys, monkeypatch):
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.domain.lifecycle import RecipeState, VersionStatus
    from rastro.domain.lifecycle_history import LifecycleHistory

    job.write_text(job.read_text().replace("[dataset]", 'recipe_memory = "r.sqlite"\n\n[dataset]'))
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    real = SQLiteRecipeRepository.history

    def with_candidate(self, key):
        history = real(self, key)
        extra = VersionStatus(version=2, state=RecipeState.CANDIDATE, validated_at=None)
        return LifecycleHistory.model_validate(
            {**history.model_dump(), "versions": (*history.versions, extra)}
        )

    monkeypatch.setattr(SQLiteRecipeRepository, "history", with_candidate)
    code, report = health(capsys, job)
    assert code == 4
    assert report["sources"][0]["quarantined_versions"] == [2]
    assert report["sources"][0]["attention"] == ["quarantined_candidate"]


def test_health_schema_is_published():
    published = json.loads(Path("docs/schemas/health.schema.json").read_text())
    assert published == job_health_json_schema()


def test_health_errors_are_json(tmp_path, capsys):
    assert main(["health", str(tmp_path / "missing.toml")]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "configuration_error"
