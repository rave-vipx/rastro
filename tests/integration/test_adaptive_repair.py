"""Repair tries the engine's adaptive tools before any model, and the run report counts it.

Every model here is a deterministic fake; nothing leaves the process.
"""

import json
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel

import rastro
from rastro.application.model_repair import assist_repair
from rastro.application.run_report import report_for_run
from rastro.domain.consistency import ConsistencyReport
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import CssRecipe, Outcome
from rastro.domain.recipe_compare import CandidateEvaluation
from rastro.domain.repair import RepairCandidate
from rastro.ports.engine import RecordCandidate

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

URL = "https://shop.example/jobs"
V1 = """<main><ul>
<li class="job"><h3 class="title">Engineer</h3><span class="city">Puerto Claro</span></li>
<li class="job"><h3 class="title">Designer</h3><span class="city">Salto</span></li>
</ul></main>"""
# A layout shift: new tags and renamed classes, the same content.
SHIFTED = """<main><div class="grid">
<div class="card-x1"><p class="name-x1">Engineer</p><em class="place-x1">Puerto Claro</em></div>
<div class="card-x1"><p class="name-x1">Designer</p><em class="place-x1">Salto</em></div>
</div></main>"""
# The titles are still there but the cities changed: the adaptive candidate misses a field.
MOVED = SHIFTED.replace("Puerto Claro", "Rio Alto").replace("Salto", "Vado")
RESCUE = {
    "strategy": "css",
    "record_selector": "div.card-x1",
    "fields": {"title": "p.name-x1::text", "city": "em.place-x1::text"},
}


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3.title::text")]
    city: Annotated[str, rastro.css(".city::text")]


class FakeModel:
    """Answers recipe requests from a script and judge requests with a match."""

    def __init__(self, proposals=()):
        self.proposals = list(proposals)
        self.requests = []

    async def generate(self, request, policy):
        self.requests.append(request)
        if request.response_schema is not None:
            names = [item["name"] for item in json.loads(request.content)["fields"]]
            return json.dumps({n: {"verdict": "match", "confidence": 0.9} for n in names})
        return json.dumps(self.proposals.pop(0) if self.proposals else {})


def scrape(html, memory, model=None):
    return rastro.scrape(html, Offer, url=URL, memory=memory, repair="auto", model=model)


def test_layout_shift_repaired_without_model(tmp_path):
    memory = tmp_path / "recipes.db"
    scrape(V1, memory)
    healed = scrape(SHIFTED, memory)
    assert healed.status == Outcome.SUCCESS and healed.repair.promoted
    assert [(o.title, o.city) for o in healed.items] == [
        ("Engineer", "Puerto Claro"),
        ("Designer", "Salto"),
    ]
    assert healed.repair.model_calls == 0


def test_model_called_only_when_candidates_fail_gates(tmp_path):
    memory = tmp_path / "recipes.db"
    scrape(V1, memory)
    idle = FakeModel()
    healed = scrape(SHIFTED, memory, idle)
    assert healed.repair.promoted and healed.repair.origin == "adaptive"
    assert healed.repair.model_calls == 0 and idle.requests == []

    other = tmp_path / "other.db"
    scrape(V1, other)
    model = FakeModel([RESCUE])
    rescued = scrape(MOVED, other, model)
    assert rescued.repair.promoted and rescued.repair.origin == "model"
    assert [o.city for o in rescued.items] == ["Rio Alto", "Vado"]
    assert model.requests


def test_report_counts_repairs_without_model(tmp_path):
    memory = tmp_path / "recipes.db"
    scrape(V1, memory)
    healed = scrape(SHIFTED, memory, FakeModel())
    run = JobRunResult.from_results(schema_fingerprint=healed.schema_fingerprint, results=[healed])
    report = report_for_run("job.toml", run)
    assert report.totals.repairs_without_model == 1
    assert report.totals.model_calls == 0
    note = report.sources[0].repair
    assert note is not None and (note.origin, note.model_calls) == ("adaptive", 0)


class Locator:
    """A fixed adaptive answer, to drive the gate order directly."""

    def similar_records(self, html, anchor_text, *, limits):
        return (RecordCandidate("div.card-x1", 2, {anchor_text: ":scope > p.name-x1"}),)

    def stable_selector(self, html, locator, *, limits):
        return None

    def anchor_by_label(self, html, record_selector, label, *, limits):
        return ":scope > em.place-x1"


def evaluation(decision: str, *reasons: str) -> CandidateEvaluation:
    return CandidateEvaluation.model_validate(
        {
            "decision": decision,
            "reasons": reasons,
            "current_status": "success",
            "current_records": 2,
            "historical_fixtures": 0,
            "historical_passed": 0,
            "indicators": ConsistencyReport(
                records=2,
                completeness=1.0,
                structural_validity=1.0,
                semantic_confidence=None,
                verdict="not_assessed",
                results=(),
            ),
            "regressions": (),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["promote", "reject"])
async def test_adaptive_candidate_meets_the_same_gates(decision):
    previous = CssRecipe(record_selector="li.job", fields={"title": "h3.title::text"}, version=1)
    seen: list[RepairCandidate] = []

    def gate(candidate):
        seen.append(candidate)
        reasons = ("all_gates_passed",) if decision == "promote" else ("regression",)
        return evaluation(decision, *reasons)

    assisted = await assist_repair(
        None,
        [],
        html=SHIFTED,
        schema=Offer,
        previous=previous,
        gate=gate,
        rows=lambda recipe: [],
        history=[{"title": "Engineer", "city": "Puerto Claro"}],
        adaptive=Locator(),
    )
    assert seen and all(c.origin == "adaptive" for c in seen)
    assert assisted.model_calls == 0
    if decision == "promote":
        assert assisted.origin == "adaptive" and len(assisted.evaluated) == 1
    else:
        assert assisted.origin is None and assisted.evaluated == []
