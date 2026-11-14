"""rastro.suggest with a model: optional examples, same verification (INT-002, AI-001).

The models are deterministic fakes; nothing leaves the process.
"""

import json

import pytest
from pydantic import BaseModel, Field

import rastro
from rastro import ConfigurationError, SuggestionError
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

PAGE = """<html><body><main>
<div class="card"><p class="name">Engineer</p><em class="place">Montevideo</em></div>
<div class="card"><p class="name">Designer</p><em class="place">Salto</em></div>
</main></body></html>"""
GOOD = {
    "strategy": "css",
    "record_selector": "div.card",
    "fields": {"title": "p.name::text", "city": "em.place::text"},
}
SWAPPED = {**GOOD, "fields": {"title": "em.place::text", "city": "p.name::text"}}


class Offer(BaseModel):
    title: str = Field(description="Job title")
    city: str


class FakeModel:
    def __init__(self, *proposals):
        self.proposals = list(proposals)
        self.requests = []

    async def generate(self, request, policy):
        self.requests.append(request)
        return json.dumps(self.proposals.pop(0) if self.proposals else {})


def test_without_examples_the_model_proposes_a_verified_recipe():
    model = FakeModel(GOOD)
    found = rastro.suggest(PAGE, Offer, model=model)
    assert [o.title for o in found.preview.items] == ["Engineer", "Designer"]
    assert {note.method for note in found.confidence.values()} == {"model"}
    hints = json.loads(model.requests[0].content)["field_hints"]
    assert hints["title"]["description"] == "Job title"


def test_examples_must_be_reproduced_by_the_model_recipe():
    model = FakeModel(SWAPPED, GOOD)
    examples = [{"title": "Engineer", "city": "Montevideo"}]

    class Refusing:
        def suggest(self, *args, **kwargs):
            raise SuggestionError("deterministic suggester found nothing")

    found = rastro.suggest(PAGE, Offer, examples=examples, model=model, _suggester=Refusing())
    assert found.recipe.fields["title"] == "p.name::text" and len(model.requests) == 2
    assert json.loads(model.requests[1].content)["last_failure"] == "examples_not_reproduced"


def test_unverifiable_proposals_raise_a_suggestion_error():
    jsonld = {"strategy": "jsonld", "fields": {"title": "/name", "city": "/city"}}
    with pytest.raises(SuggestionError, match="css_recipe_required"):
        rastro.suggest(PAGE, Offer, model=FakeModel(jsonld, jsonld))
    with pytest.raises(SuggestionError, match="too_few_records"):
        rastro.suggest(PAGE, Offer, model=FakeModel(GOOD, GOOD), records_min=3)


def test_examples_are_required_without_a_model():
    with pytest.raises(ConfigurationError, match="model"):
        rastro.suggest(PAGE, Offer)


def test_cli_suggest_with_model_and_fields(tmp_path, monkeypatch, capsys):
    calls = []

    def factory(spec=None, **options):
        calls.append((spec, options))
        return FakeModel(GOOD)

    monkeypatch.setattr(rastro, "model", factory, raising=False)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    args = ["suggest", str(page), "--model", "local-spec", "--field", "title", "--field", "city"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 2
    assert calls == [("local-spec", {})]
    assert main(["suggest", str(page), "--field", "title"]) == 2


def test_cli_new_with_model_writes_a_job(tmp_path, monkeypatch):
    monkeypatch.setattr(rastro, "model", lambda spec=None, **o: FakeModel(GOOD), raising=False)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    job = tmp_path / "job.toml"
    args = ["new", str(job), "--from", str(page), "--model", "env", "--field", "title"]
    assert main([*args, "--field", "city"]) == 0
    assert 'title = "p.name::text"' in job.read_text(encoding="utf-8")


def test_a_costly_model_selector_is_refused_before_verification():
    """Security audit H3: suggest never evaluates a model's :has() selector."""
    costly = {**GOOD, "record_selector": "div.card:has(p.name)"}
    with pytest.raises(SuggestionError, match="invalid_model_candidate"):
        rastro.suggest(PAGE, Offer, model=FakeModel(costly, costly))
    model = FakeModel(costly, GOOD)
    assert rastro.suggest(PAGE, Offer, model=model).recipe.record_selector == "div.card"
