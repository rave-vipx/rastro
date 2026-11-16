"""COST-002, OBS-001: one model budget and one remote notice per load, not per source.

``load_job_file`` runs the sources one by one (SCHED-004); the model is built once for the
whole load and shared by every source. The models are deterministic fakes.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import rastro
from rastro.application.scheduler import watch_job
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

JOB = """format_version = 1
discover_recipes = true

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["name"]
{model}
{sources}
[[fields]]
name = "name"
type = "string"
"""
MODEL = """
[model]
provider = "ollama"
model = "qwen3:4b"
cost_per_call = 1.0
{limit}
"""
SOURCE = """
[[sources]]
kind = "file"
path = "page{n}.html"
url = "https://example.test/items/{n}"
"""
PAGE = '<html><body><p class="n">Item {n}</p></body></html>'
PROPOSAL = {
    "strategy": "css",
    "record_selector": "p.n",
    "fields": {"name": "::text"},
}


class Fake:
    def __init__(self, *, remote: bool = False) -> None:
        self.remote, self.calls = remote, 0

    async def generate(self, request, policy):
        self.calls += 1
        if request.response_schema is None:
            return json.dumps(PROPOSAL)
        names = [item["name"] for item in json.loads(request.content)["fields"]]
        return json.dumps({n: {"verdict": "match", "confidence": 1.0} for n in names})


def make_job(tmp_path: Path, *, model: str = "", sources: int = 3) -> Path:
    for n in range(1, sources + 1):
        (tmp_path / f"page{n}.html").write_text(PAGE.format(n=n), encoding="utf-8")
    text = JOB.format(
        model=model, sources="".join(SOURCE.format(n=n) for n in range(1, sources + 1))
    )
    path = tmp_path / "job.toml"
    path.write_text(text, encoding="utf-8")
    return path


def reasons(loaded) -> list[str | None]:
    return [source.reason for source in loaded.sources]


@pytest.fixture
def fake(monkeypatch):
    model = Fake()
    monkeypatch.setattr("rastro.model_factory.build_model", lambda spec: model)
    return model


def test_a_job_model_budget_spans_every_source_of_a_load(tmp_path, fake):
    job = make_job(tmp_path, model=MODEL.format(limit="max_cost = 2.0"))
    loaded = rastro.load_job_file(job)
    assert fake.calls == 2
    assert reasons(loaded) == [None, None, "model_budget_exhausted"]


def test_a_job_model_without_a_limit_is_called_for_every_source(tmp_path, fake):
    loaded = rastro.load_job_file(make_job(tmp_path, model=MODEL.format(limit="")))
    assert fake.calls == 3
    assert reasons(loaded) == [None, None, None]


def test_the_remote_notice_prints_once_per_load(tmp_path, monkeypatch, capsys):
    remote = Fake(remote=True)
    monkeypatch.setattr("rastro.model_factory.build_model", lambda spec: remote)
    rastro.load_job_file(make_job(tmp_path, model=MODEL.format(limit="")), allow_remote=True)
    notices = [ln for ln in capsys.readouterr().err.splitlines() if "remote model" in ln]
    assert len(notices) == 1 and remote.calls == 3


def test_the_operator_limit_through_job_load_spans_every_source(tmp_path, fake):
    job = make_job(tmp_path, model=MODEL.format(limit=""))
    code = main(["job", "load", str(job), "--max-model-cost", "2", "--model-cost-per-call", "1"])
    assert fake.calls == 2 and code != 0


def test_the_sdk_operator_limit_spans_every_source(tmp_path, fake):
    job = make_job(tmp_path, model=MODEL.format(limit=""))
    loaded = rastro.load_job_file(job, max_model_cost=2.0, model_cost_per_call=1.0)
    assert fake.calls == 2
    assert reasons(loaded)[2] == "model_budget_exhausted"


def test_a_watch_tick_forwards_the_cost_keywords(tmp_path, fake):
    job = make_job(tmp_path, model=MODEL.format(limit="") + '\n[schedule]\nevery = "1h"\n')
    results = []
    watch_job(
        job,
        once=True,
        max_model_cost=2.0,
        model_cost_per_call=1.0,
        on_tick=results.append,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert fake.calls == 2 and reasons(results[0])[2] == "model_budget_exhausted"
