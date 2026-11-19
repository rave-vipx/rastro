"""COST-002, OBS-001: the model cost cap and remote notice cover operator-supplied models.

An operator model (SDK ``model=``, ``recipe repair --model``, ``RASTRO_MODEL``) is wrapped
the same way as a job-declared one. The models are deterministic fakes.
"""

import json

import pytest

import rastro
from rastro.cli import main
from rastro.domain.models import ConfigurationError
from tests.integration.test_job_repair_model import PROPOSAL, make_job

pytestmark = pytest.mark.usefixtures("offline_cli_loop", "model_path_only")


class Fake:
    def __init__(self, *, remote: bool = False) -> None:
        self.remote, self.calls = remote, 0

    async def generate(self, request, policy):
        self.calls += 1
        if request.response_schema is None:
            return json.dumps(PROPOSAL)
        names = [item["name"] for item in json.loads(request.content)["fields"]]
        return json.dumps({n: {"verdict": "match", "confidence": 1.0} for n in names})


def issue_codes(report) -> list[str]:
    return [issue.code for source in report.sources for issue in source.issues]


def test_operator_model_stops_at_the_cost_limit(tmp_path):
    fake = Fake()
    report = rastro.repair_job_source(
        make_job(tmp_path), 1, model=fake, max_model_cost=0.5, model_cost_per_call=1.0
    )
    assert fake.calls == 0
    assert "model_budget_exhausted" in issue_codes(report)


def test_operator_model_without_a_price_is_a_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError, match="cost_per_call"):
        rastro.repair_job_source(make_job(tmp_path), 1, model=Fake(), max_model_cost=0.5)


def test_price_alone_does_not_stop_the_model(tmp_path):
    fake = Fake()
    rastro.repair_job_source(make_job(tmp_path), 1, model=fake, model_cost_per_call=1.0)
    assert fake.calls == 2


def test_remote_operator_model_prints_the_notice_once(tmp_path, capsys):
    rastro.repair_job_source(make_job(tmp_path), 1, model=Fake(remote=True))
    notices = [line for line in capsys.readouterr().err.splitlines() if "remote model" in line]
    assert len(notices) == 1 and "limit none" in notices[0]


def test_local_operator_model_prints_no_notice(tmp_path, capsys):
    rastro.repair_job_source(make_job(tmp_path), 1, model=Fake(remote=False))
    assert "remote model" not in capsys.readouterr().err


def test_cli_repair_has_the_cost_flags(tmp_path, monkeypatch, capsys):
    fake = Fake()
    monkeypatch.setattr("rastro.model_factory.build_model", lambda spec: fake)
    job = make_job(tmp_path)
    main(
        [
            "recipe", "repair", str(job), "--model", "ollama:q",
            "--max-model-cost", "0.5", "--model-cost-per-call", "1.0",
        ]
    )  # fmt: skip
    assert fake.calls == 0 and "model_budget_exhausted" in capsys.readouterr().out


def test_cli_repair_limit_without_a_price_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("rastro.model_factory.build_model", lambda spec: Fake())
    code = main(["recipe", "repair", str(make_job(tmp_path)), "--model", "ollama:q",
                 "--max-model-cost", "0.5"])  # fmt: skip
    assert code == 2 and "cost_per_call" in capsys.readouterr().err


JOB_MODELLESS = """format_version = 1
discover_recipes = true

[[sources]]
kind = "file"
path = "page.html"
url = "https://example.test/items"

[[fields]]
name = "name"
type = "string"
"""


def test_run_job_with_an_sdk_model_is_capped(tmp_path):
    (tmp_path / "page.html").write_text("<html><body><p>nothing here</p></body></html>")
    (tmp_path / "job.toml").write_text(JOB_MODELLESS)
    fake = Fake()
    result = rastro.run_job_file(
        tmp_path / "job.toml", model=fake, max_model_cost=0.5, model_cost_per_call=1.0
    )
    assert fake.calls == 0
    assert "model_budget_exhausted" in [i.code for r in result.results for i in r.issues]


def test_run_job_with_an_sdk_model_and_no_price_is_an_error(tmp_path):
    (tmp_path / "page.html").write_text("<html><body><p>x</p></body></html>")
    (tmp_path / "job.toml").write_text(JOB_MODELLESS)
    with pytest.raises(ConfigurationError, match="cost_per_call"):
        rastro.run_job_file(tmp_path / "job.toml", model=Fake(), max_model_cost=0.5)


def test_suggest_and_scrape_models_are_wrapped_too(capsys):
    from rastro.application.model_budget import BudgetedModel, model_cost_override
    from rastro.application.model_choice import resolve_model

    assert isinstance(resolve_model(Fake(remote=True)), BudgetedModel)
    local = Fake(remote=False)
    assert resolve_model(local) is local
    with model_cost_override(0.5), pytest.raises(ConfigurationError, match="cost_per_call"):
        resolve_model(local)
