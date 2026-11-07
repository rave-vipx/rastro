"""R2 acceptance with real parser/lifecycle and a small R3 gate fake (REPAIR-001/004).

The fake establishes the handoff contract, not full recipe CI correctness; R3
must run historical, schema and semantic evaluation in its own acceptance suite.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rastro import Limits, Outcome, Source
from rastro.adapters.discovery import ScraplingDiscovery
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.extraction import extract_document
from rastro.application.intelligence import with_version
from rastro.application.repair_candidates import RepairCandidateGenerator
from rastro.domain.job_fields import build_job_schema
from rastro.domain.jobs import parse_job
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey
from rastro.mocklab.mutations.html_content import HtmlChangeClasses
from rastro.mocklab.mutations.html_structure import HtmlWrapElement
from rastro.mocklab.mutations.plan import MutationPlan

SAMPLE = Path(__file__).parents[2] / "examples/uy-dance-agenda/sample"


class ThresholdGateFake:
    def __init__(self, threshold):
        self.threshold = threshold

    def evaluate(self, result, candidate):
        # The real R3 evaluator supplies the measured confidence from its full CI suite.
        confidence = 0.95 if result.status == Outcome.SUCCESS else 0.0
        return not candidate.ambiguous and confidence >= self.threshold


@pytest.mark.asyncio
@pytest.mark.parametrize(("threshold", "serving_version"), [(0.90, 2), (0.99, 1)])
async def test_relocation_candidate_recovers_moved_node_and_is_promoted_only_above_threshold(
    tmp_path, threshold, serving_version
):
    job = parse_job((SAMPLE / "job.toml").read_text(), format="toml")
    schema = build_job_schema(job.fields)
    recipe = job.sources[0].recipe
    source = Source(url=job.sources[0].url, **job.region.model_dump())
    html = (SAMPLE / "sodre-cartelera.html").read_text()
    parser, adapter, limits = ScraplingParser(), ScraplingRelocation(), Limits()
    before = extract_document(
        html, source=source, schema=schema, recipe=recipe, parser=parser, limits=limits
    )
    assert before.status == Outcome.SUCCESS
    snapshot = adapter.capture(html, source=source, recipe=recipe, result=before, limits=limits)
    changed = (
        MutationPlan(
            seed=42,
            operators=(
                HtmlWrapElement(selector="h2.cyg--display-3", wrapper_tag="section"),
                HtmlChangeClasses(
                    selector="h2.cyg--display-3", rename={"cyg--display-3": "new-title"}
                ),
            ),
        )
        .apply(html)
        .document
    )
    failed = extract_document(
        changed, source=source, schema=schema, recipe=recipe, parser=parser, limits=limits
    )
    assert failed.status != Outcome.SUCCESS
    repository = SQLiteRecipeRepository(tmp_path / "recipes.sqlite")
    key = RecipeKey.for_source(source, schema)
    now = datetime.now(UTC)
    repository.save_success(key, recipe, selected_index=0, at=now)
    generator = RepairCandidateGenerator(discovery=ScraplingDiscovery(), relocation=adapter)
    report = await generator.generate(
        changed, source=source, schema=schema, recipe=recipe, limits=limits, snapshot=snapshot
    )
    candidate = report.candidates[0]
    assert candidate.origin == "scrapling_relocation"
    repaired = with_version(candidate.recipe, repository.next_version(key))
    current = extract_document(
        changed, source=source, schema=schema, recipe=repaired, parser=parser, limits=limits
    )
    assert current.status == Outcome.SUCCESS
    assert [r.data for r in current.records] == [r.data for r in before.records]
    assert all(r.evidence["title"].recipe_version == 2 for r in current.records)
    assert report.inference_calls == 0
    repository.save_candidate(key, repaired, selected_index=0, at=now)
    assert repository.get(key).recipe.version == 1
    if ThresholdGateFake(threshold).evaluate(current, candidate):
        repository.transition(key, 2, RecipeState.ACTIVE, reason="Test gate passed", at=now)
    assert repository.get(key).recipe.version == serving_version
