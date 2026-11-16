"""COST-002: remote-model cost notice and an enforced model cost limit."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from rastro import InferencePolicy, ModelError, ModelRequest
from rastro.application.model_budget import BudgetedModel, budget_for, model_cost_override
from rastro.cli import main
from rastro.domain.intelligence import ModelBudget
from rastro.domain.job_providers import OllamaProvider
from rastro.domain.models import ConfigurationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

REQUEST = ModelRequest(system="s", content="c")
POLICY = InferencePolicy()


class Fake:
    def __init__(self, *, remote: bool, output: str = "{}") -> None:
        self.remote, self.calls, self.output = remote, 0, output

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        return self.output


def wrap(fake: Fake, **budget) -> BudgetedModel:
    return BudgetedModel(
        fake,
        ModelBudget(**budget),
        name="gpt-x",
        operation="recipe proposal",
        remote=fake.remote,
        calls_per_source=2,
    )


def call(model: BudgetedModel) -> str:
    return asyncio.run(
        model.generate(REQUEST, POLICY),
        loop_factory=lambda: asyncio.SelectorEventLoop(),
    )


def test_remote_model_prints_one_notice_before_the_first_call(capsys):
    fake = Fake(remote=True)
    model = wrap(fake, cost_per_call=0.002, max_cost=0.01)
    assert capsys.readouterr().err == ""  # building a model prints nothing
    call(model)
    call(model)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and fake.calls == 2
    for part in ("recipe proposal", "gpt-x", "max calls 5", "$0.0100", "limit $0.0100"):
        assert part in err


def test_remote_without_prices_says_cost_is_unknown(capsys):
    call(wrap(Fake(remote=True)))
    err = capsys.readouterr().err
    assert "unknown" in err and "limit none" in err and "max calls 2 per source" in err


def test_local_model_has_no_notice_but_still_honors_the_limit(capsys):
    fake = Fake(remote=False)
    model = wrap(fake, cost_per_call=1.0, max_cost=2.0)
    call(model)
    call(model)
    with pytest.raises(ModelError) as stop:
        call(model)
    assert stop.value.code == "model_budget_exhausted"
    assert fake.calls == 2  # the call that would exceed the limit never reaches the model
    assert capsys.readouterr().err == ""


def test_a_limit_below_one_call_stops_before_the_first_call():
    fake = Fake(remote=False)
    with pytest.raises(ModelError, match="model_budget_exhausted"):
        call(wrap(fake, cost_per_call=0.5, max_cost=0.4))
    assert fake.calls == 0


def test_float_rounding_does_not_lose_the_last_call():
    fake = Fake(remote=False)
    model = wrap(fake, cost_per_call=0.1, max_cost=0.3)  # 0.3 / 0.1 is 2.999...
    for _ in range(3):
        call(model)
    assert fake.calls == 3


@pytest.mark.parametrize(
    "budget",
    [
        {"max_cost": 1.0},
        {"cost_per_call": -1},
        {"cost_per_call": 1, "max_cost": 0},
        {"cost_per_call": float("nan")},
        {"cost_per_call": 1, "max_cost": float("inf")},
    ],
)
def test_invalid_budgets_are_rejected(budget):
    with pytest.raises(ValidationError):
        ModelBudget(**budget)


def test_cli_override_beats_the_job_value_and_needs_a_price():
    spec = OllamaProvider(model="q", cost_per_call=0.5, max_cost=5.0)
    assert budget_for(spec, None).max_cost == 5.0
    with model_cost_override(1.0):
        assert budget_for(spec, None).max_cost == 1.0
    with pytest.raises(ConfigurationError, match="cost_per_call"), model_cost_override(1.0):
        budget_for(OllamaProvider(model="q"), None)


JOB = """format_version = 1
discover_recipes = true

[[sources]]
path = "page.html"
url = "https://example.test/items"

[[fields]]
name = "name"
type = "string"

[model]
provider = "ollama"
model = "qwen3:4b"
cost_per_call = 0.5
max_cost = {limit}
"""


@pytest.fixture
def job(tmp_path, monkeypatch):
    (tmp_path / "page.html").write_text("<html><body><p>nothing here</p></body></html>")
    fake = Fake(remote=False, output="not json")
    monkeypatch.setattr("rastro.model_factory.build_model", lambda spec: fake)
    path = tmp_path / "job.toml"
    path.write_text(JOB.format(limit=0.5))
    return path, fake


def codes(capsys):
    (result,) = json.loads(capsys.readouterr().out)["results"]
    return result["status"], [i["code"] for i in result["issues"]]


def test_job_stops_calling_the_model_with_an_explicit_outcome(job, capsys):
    job, fake = job
    main(["job", "run", str(job)])
    status, issues = codes(capsys)
    assert fake.calls == 1  # the policy allowed two; the budget allowed one
    assert status == "insufficient_evidence" and "model_budget_exhausted" in issues


def test_max_model_cost_flag_raises_the_limit(job, capsys):
    job, fake = job
    main(["job", "run", str(job), "--max-model-cost", "1.0"])
    _, issues = codes(capsys)
    assert fake.calls == 2 and "model_budget_exhausted" not in issues
    main(["job", "run", str(job), "--max-model-cost", "0.25"])
    assert codes(capsys)[1].count("model_budget_exhausted") == 1
