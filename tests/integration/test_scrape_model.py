"""rastro.scrape with a model: zero-selector recipes, model repair, semantic gate.

Requirements: INT-002, REPAIR-001, REPAIR-002, QUAL-005, QUAL-006, AI-001. Every model
here is a deterministic fake ModelPort; nothing leaves the process.
"""

import json
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field

import rastro
from rastro import Outcome, SQLiteRecipeRepository
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.domain.repair import RepairCandidate

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

URL = "https://acme.example/careers"
V1 = """<main><ul>
<li class="job"><h3 class="title">Engineer</h3><span class="city">Montevideo</span></li>
<li class="job"><h3 class="title">Designer</h3><span class="city">Salto</span></li>
</ul></main>"""
V2 = V1.replace('<h3 class="title">', '<h2 class="heading">').replace("</h3>", "</h2>")
# The renamed title now sits next to a district label that no field ever read.
V2_AREA = V2.replace(
    '<span class="city">Montevideo</span>',
    '<span class="city">Montevideo</span><i class="area">Centro</i>',
).replace(
    '<span class="city">Salto</span>', '<span class="city">Salto</span><i class="area">Norte</i>'
)
# Fully rewritten: list items became a grid of cards with new tags and classes.
V3 = """<main><div class="grid">
<div class="card"><p class="name">Engineer</p><em class="place">Montevideo</em></div>
<div class="card"><p class="name">Designer</p><em class="place">Salto</em></div>
</div></main>"""
# The model sees only the new page; Rastro unions each locator with the serving one.
RESCUE = {
    "strategy": "css",
    "record_selector": "div.card",
    "fields": {"title": "p.name::text", "city": "em.place::text"},
}
SWAPPED = {
    "strategy": "css",
    "record_selector": "div.card",
    "fields": {"title": "em.place::text", "city": "p.name::text"},
}


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3.title::text"), Field(description="Job title")]
    city: Annotated[str, rastro.css(".city::text"), Field(description="City of the job")]


class FakeModel:
    """Answers recipe requests from a script and judge requests with one verdict."""

    def __init__(self, proposals=(), verdict="match"):
        self.proposals = list(proposals)
        self.verdict = verdict
        self.requests = []

    async def generate(self, request, policy):
        self.requests.append(request)
        if request.response_schema is not None:
            names = [item["name"] for item in json.loads(request.content)["fields"]]
            return json.dumps({n: {"verdict": self.verdict, "confidence": 0.9} for n in names})
        item = self.proposals.pop(0) if self.proposals else {}
        return item if isinstance(item, str) else json.dumps(item)

    @property
    def judged(self):
        return [r for r in self.requests if r.response_schema is not None]


def _scrape(html, memory, model=None, repair="auto"):
    return rastro.scrape(html, Offer, url=URL, memory=memory, repair=repair, model=model)


def test_model_rescues_a_rewrite_relocation_cannot_repair(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    assert _scrape(V3, memory).repair.promoted is False  # deterministic repair gives up
    model = FakeModel([RESCUE])
    healed = _scrape(V3, memory, model)
    assert healed.status == Outcome.SUCCESS
    assert [(o.title, o.city) for o in healed.items] == [
        ("Engineer", "Montevideo"),
        ("Designer", "Salto"),
    ]
    assert healed.repair.promoted and healed.repair.origin == "model"
    assert healed.repair.model_calls == 2  # one proposal, one semantic judgement
    served = SQLiteRecipeRepository(memory).get(_key(memory)).recipe
    assert served.record_selector == "li.job, div.card"
    assert served.fields["title"] == "h3.title::text, p.name::text"
    proposal = json.loads(model.requests[0].content)
    assert proposal["previous_recipe"]["record_selector"] == "li.job"
    assert proposal["field_hints"]["title"] == {"type": "str", "description": "Job title"}


def _misplace_title(monkeypatch):
    """A relocation mistake: the title falls back to the district label on the new page."""

    def relocate(self, html, recipe, snapshot, limits):
        fields = {**recipe.fields, "title": "h3.title::text, i.area::text"}
        wrong = recipe.model_copy(update={"fields": fields})
        return (RepairCandidate(recipe=wrong, origin="scrapling_relocation", rationale="test"),)

    monkeypatch.setattr(ScraplingRelocation, "relocate", relocate)


def test_without_a_model_shape_gates_promote_a_wrong_title(tmp_path, monkeypatch):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    _misplace_title(monkeypatch)
    wrong = _scrape(V2_AREA, memory)
    assert wrong.repair.promoted and wrong.repair.origin is None
    assert [o.title for o in wrong.items] == ["Centro", "Norte"]


def test_semantic_judge_blocks_a_wrong_title_that_passes_shape_gates(tmp_path, monkeypatch):
    """QUAL-005: a relocated candidate is judged too; a mismatch vetoes it."""
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    _misplace_title(monkeypatch)
    model = FakeModel(verdict="mismatch")
    blocked = _scrape(V2_AREA, memory, model)
    assert blocked.items == [] and not blocked.repair.promoted
    (candidate,) = blocked.repair.candidates
    assert candidate.decision == "quarantine" and "semantic_mismatch" in candidate.reasons
    assert blocked.repair.origin == "relocation" and blocked.repair.model_calls == 1
    (request,) = model.judged
    (field,) = json.loads(request.content)["fields"]
    assert field["name"] == "title" and field["reference_values"] == ["Engineer", "Designer"]
    assert field["untrusted_candidate_values"] == ["Centro", "Norte"]
    assert SQLiteRecipeRepository(memory).get(_key(memory)).recipe.version == 1


def test_a_recipe_that_still_serves_never_calls_the_judge(tmp_path):
    """QUAL-005: no repair, no judgement."""
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    model = FakeModel()
    again = _scrape(V1, memory, model)
    assert again.status == Outcome.SUCCESS and again.repair is None and model.requests == []


def test_semantic_judge_blocks_a_model_candidate_that_passes_value_checks(tmp_path):
    """QUAL-005, QUAL-006: the judge vetoes after every deterministic check passed."""
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    model = FakeModel([RESCUE], verdict="mismatch")
    blocked = _scrape(V3, memory, model)
    assert blocked.items == [] and not blocked.repair.promoted
    (candidate,) = blocked.repair.candidates
    assert candidate.decision == "quarantine" and "semantic_mismatch" in candidate.reasons
    assert blocked.repair.origin == "model" and blocked.repair.model_calls == 2
    (request,) = model.judged
    fields = {f["name"]: f for f in json.loads(request.content)["fields"]}
    assert fields["title"]["reference_values"] == ["Engineer", "Designer"]
    assert fields["title"]["untrusted_candidate_values"] == ["Engineer", "Designer"]
    assert SQLiteRecipeRepository(memory).get(_key(memory)).recipe.version == 1


def _key(memory):
    from rastro import RecipeKey, Source
    from rastro.domain.model_selectors import recipe_from_model
    from rastro.scrape import _context

    context = _context(recipe_from_model(Offer))
    return RecipeKey.for_source(Source(url=URL, locale="en-US"), Offer, context)


@pytest.mark.parametrize(
    ("verdict", "promoted", "reason"),
    [("unsure", True, None), ("broken", False, "semantic_judge_failed")],
)
def test_unsure_promotes_only_on_strong_gates_and_failure_blocks(
    tmp_path, verdict, promoted, reason
):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    result = _scrape(V3, memory, FakeModel([RESCUE], verdict=verdict))
    assert result.repair.promoted is promoted
    if reason:
        assert reason in result.repair.candidates[0].reasons


def test_wrong_model_output_is_rejected_within_the_call_budget(tmp_path):
    memory = tmp_path / "recipes.db"
    _scrape(V1, memory)
    model = FakeModel([SWAPPED, "not json", RESCUE])
    failed = _scrape(V3, memory, model)
    assert failed.items == [] and not failed.repair.promoted
    assert failed.repair.model_calls == 2 and len(model.requests) == 2  # budget: 2 calls
    (swapped,) = failed.repair.candidates
    assert swapped.decision == "reject" and "value_matches_other_field" in swapped.reasons
    assert json.loads(model.requests[1].content)["last_failure"] == "candidate_failed_recipe_ci"
    assert SQLiteRecipeRepository(memory).get(_key(memory)).recipe.version == 1


class Plain(BaseModel):
    title: str = Field(description="Job title")
    city: str


def test_zero_selector_scrape_learns_a_recipe_from_the_model(tmp_path):
    with pytest.raises(rastro.ConfigurationError):
        rastro.scrape(V1, Plain, url=URL)
    model = FakeModel(
        [
            {
                "strategy": "css",
                "record_selector": "li.job",
                "fields": {"title": "h3.title::text", "city": ".city::text"},
            }
        ]
    )
    memory = tmp_path / "recipes.db"
    first = rastro.scrape(V1, Plain, url=URL, memory=memory, model=model)
    assert first.status == Outcome.SUCCESS and first.learned_recipe is not None
    assert [p.title for p in first.items] == ["Engineer", "Designer"]
    assert first.metrics.inference_calls == 1
    again = rastro.scrape(V1, Plain, url=URL, memory=memory, model=FakeModel())
    assert again.recipe_reused and again.metrics.inference_calls == 0


def test_model_argument_is_validated():
    with pytest.raises(rastro.ConfigurationError, match="ModelPort"):
        rastro.scrape(V1, Offer, url=URL, model=object())
