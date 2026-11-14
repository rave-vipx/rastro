"""Job repair with a model: escalation after relocation, history unions, semantic gate.

Requirements: REPAIR-001, REPAIR-002, REPAIR-005, QUAL-005, QUAL-006, AI-001. The models
are deterministic fakes; nothing leaves the process.
"""

import json
import sys

import pytest

import rastro
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

JOB = """format_version = 1
recipe_memory = "recipes.sqlite"
{repair}
[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["job_id"]

[[sources]]
kind = "file"
path = "board.html"
url = "https://example.test/jobs"

[sources.recipe]
version = 1
strategy = "css"
record_selector = "article.card"
require_evidence = true

[sources.recipe.fields]
job_id = "::attr(data-id)"
title = "h2.title::text"

[[fields]]
name = "job_id"
type = "string"

[[fields]]
name = "title"
type = "string"
"""
OLD = '<article class="card" data-id="job-{n}"><h2 class="title">Role {n}</h2></article>'
# Rewritten markup: new tags and classes everywhere, so relocation finds nothing.
NEW = '<div class="row" data-id="job-{n}"><p class="role">Role {n}</p></div>'
PROPOSAL = {
    "strategy": "css",
    "record_selector": "div.row",
    "fields": {"job_id": "::attr(data-id)", "title": "p.role::text"},
}


def page(card):
    return (
        "<html><body><main>"
        + "".join(card.format(n=n) for n in range(1, 5))
        + "</main></body></html>"
    )


class FakeModel:
    def __init__(self, verdict="match"):
        self.verdict = verdict
        self.requests = []

    async def generate(self, request, policy):
        self.requests.append(request)
        if request.response_schema is None:
            return json.dumps(PROPOSAL)
        names = [item["name"] for item in json.loads(request.content)["fields"]]
        return json.dumps({n: {"verdict": self.verdict, "confidence": 1.0} for n in names})


def make_job(tmp_path, repair="[repair]\nauto_promote = true\n"):
    (tmp_path / "board.html").write_text(page(OLD), encoding="utf-8")
    path = tmp_path / "job.toml"
    path.write_text(JOB.format(repair=repair), encoding="utf-8")
    assert rastro.load_job_file(path).job.status == "success"
    (tmp_path / "board.html").write_text(page(NEW), encoding="utf-8")
    return path


def test_without_a_model_the_rewrite_is_not_repaired(tmp_path):
    report = rastro.repair_job_source(make_job(tmp_path), 1)
    assert report.sources[0].promoted_version is None
    assert "origin" not in report.sources[0].model_dump(mode="json")


def test_model_override_repairs_with_a_history_compatible_union(tmp_path):
    job = make_job(tmp_path)
    model = FakeModel()
    (source,) = rastro.repair_job_source(job, 1, model=model).sources
    assert source.promoted_version is not None and source.origin == "model"
    assert source.inference_calls == 2  # one proposal, one judgement
    (served,) = [c for c in source.comparisons if c.evaluation.decision == "promote"]
    assert served.changed_fields == ("title",)
    loaded = rastro.load_job_file(job)
    assert loaded.job.status == "success" and loaded.sources[0].recipe_origin == "memory"
    assert json.loads(model.requests[0].content)["previous_recipe"]["record_selector"] == (
        "article.card"
    )


def test_semantic_mismatch_quarantines_the_model_candidate(tmp_path):
    (source,) = rastro.repair_job_source(make_job(tmp_path), 1, model=FakeModel("mismatch")).sources
    assert source.promoted_version is None
    (candidate,) = source.comparisons
    assert candidate.evaluation.decision == "quarantine"
    assert "semantic_mismatch" in candidate.evaluation.reasons
    judged = [r for r in candidate.evaluation.indicators.results if r.kind == "model_judge"]
    assert [r.status for r in judged] == ["violated"]


def test_repair_model_flag_uses_the_model_factory_with_rastro_model(tmp_path, monkeypatch):
    calls = []

    def factory(spec=None, **options):
        calls.append((spec, options))
        return FakeModel()

    monkeypatch.setattr(rastro, "model", factory, raising=False)
    job = make_job(tmp_path, "[repair]\nauto_promote = true\nmodel = true\n")
    assert rastro.repair_job_source(job, 1).sources[0].promoted_version is not None
    assert calls == [(None, {})]


def test_cli_model_option_passes_the_spec_and_remote_opt_in(tmp_path, monkeypatch, capsys):
    calls = []

    def factory(spec=None, **options):
        calls.append((spec, options))
        return FakeModel()

    monkeypatch.setattr(rastro, "model", factory, raising=False)
    job = make_job(tmp_path)
    code = main(["recipe", "repair", str(job), "--model", "http:remote", "--allow-remote"])
    assert code == 0 and calls == [("http:remote", {"allow_remote": True})]
    assert json.loads(capsys.readouterr().out)["sources"][0]["origin"] == "model"


def test_model_specs_need_the_factory(tmp_path, monkeypatch):
    monkeypatch.delattr(rastro, "model", raising=False)
    with pytest.raises(rastro.ConfigurationError, match=r"rastro\.model"):
        rastro.repair_job_source(make_job(tmp_path), 1, model="spec")


class InvalidSelectorModel(FakeModel):
    async def generate(self, request, policy):
        self.requests.append(request)
        bad = {**PROPOSAL, "fields": {**PROPOSAL["fields"], "title": "h2..title::text"}}
        return json.dumps(bad)


def test_an_invalid_model_selector_rejects_the_candidate_not_the_run(tmp_path):
    model = InvalidSelectorModel()
    (source,) = rastro.repair_job_source(make_job(tmp_path), 1, model=model).sources
    assert source.promoted_version is None and len(model.requests) == 2
    assert [issue.code for issue in source.issues] == ["invalid_model_candidate"]


def test_a_costly_model_selector_is_never_stored_or_evaluated(tmp_path, monkeypatch):
    """Security audit H3: repair escalation applies the untrusted-recipe policy."""
    costly = {**PROPOSAL, "record_selector": "div.row:has(p.role)"}
    monkeypatch.setattr(sys.modules[__name__], "PROPOSAL", costly)
    job = make_job(tmp_path)
    (source,) = rastro.repair_job_source(job, 1, model=FakeModel()).sources
    assert source.outcome == "no_candidates" and source.comparisons == ()
    assert [issue.code for issue in source.issues] == ["invalid_model_candidate"]
    assert source.inference_calls == 2  # both proposals refused within the call budget
