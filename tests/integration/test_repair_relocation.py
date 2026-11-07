"""Real relocation, historical replay and lifecycle gates (REPAIR-001/002/004)."""

from pathlib import Path

import pytest

from rastro import Limits, Outcome, Source
from rastro.adapters.discovery import ScraplingDiscovery
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.extraction import extract_document
from rastro.application.intelligence import with_version
from rastro.application.recipe_ci import evaluate_candidate
from rastro.application.repair_candidates import RepairCandidateGenerator
from rastro.domain.job_fields import build_job_schema
from rastro.domain.jobs import parse_job
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey
from rastro.domain.recipe_compare import RepairPolicy
from rastro.mocklab.fixtures import build_fixture
from rastro.mocklab.mutations.html_content import HtmlChangeClasses
from rastro.mocklab.mutations.html_structure import HtmlWrapElement
from rastro.mocklab.mutations.plan import MutationPlan

SAMPLE = Path(__file__).parents[2] / "examples/uy-dance-agenda/sample"


@pytest.mark.asyncio
@pytest.mark.parametrize(("minimum_records", "serving_version"), [(4, 2), (5, 1)])
async def test_relocation_candidate_recovers_moved_node_and_is_promoted_only_above_threshold(
    tmp_path, minimum_records, serving_version
):
    job = parse_job((SAMPLE / "job.toml").read_text(encoding="utf-8"), format="toml")
    schema = build_job_schema(job.fields)
    recipe = job.sources[0].recipe
    source = Source(url=job.sources[0].url, **job.region.model_dump())
    html = (SAMPLE / "sodre-cartelera.html").read_text(encoding="utf-8")
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
    now = before.completed_at
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
    fixture = build_fixture(
        html, source=source, schema=schema, recipe=recipe, result=before, synthetic_count=0
    )
    evaluation = evaluate_candidate(
        candidate,
        changed,
        source=source,
        schema=schema,
        parser=parser,
        limits=limits,
        observed_at=now,
        fixtures=(fixture,),
        policy=RepairPolicy(auto_promote=True, min_records=minimum_records),
    )
    assert evaluation.historical_passed == 1
    if evaluation.decision == "promote":
        repository.transition(key, 2, RecipeState.ACTIVE, reason="Test gate passed", at=now)
    assert repository.get(key).recipe.version == serving_version
