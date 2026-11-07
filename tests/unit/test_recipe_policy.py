"""REPAIR-002/004/005: thresholds and generated regressions use measured evidence."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro.adapters.scrapling import ScraplingParser
from rastro.application.extraction import extract_document
from rastro.application.recipe_ci import evaluate_candidate
from rastro.domain.consistency import AfterObservation
from rastro.domain.models import ConfigurationError, CssRecipe, Limits, Source
from rastro.domain.recipe_compare import RepairPolicy
from rastro.domain.repair import RepairCandidate
from rastro.mocklab.fixtures import build_fixture


class Item(BaseModel):
    title: str
    extra: str | None = None


SOURCE = Source(url="https://example.com/")
RECIPE = CssRecipe(record_selector="article", fields={"title": "h2::text"}, require_evidence=True)
BODY = "<article><h2>Hello</h2></article>"
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def fixture():
    result = extract_document(
        BODY, source=SOURCE, schema=Item, recipe=RECIPE, parser=ScraplingParser(), limits=Limits()
    )
    return build_fixture(
        BODY, source=SOURCE, schema=Item, recipe=RECIPE, result=result, synthetic_count=0
    )


def evaluate(recipe=RECIPE, **kwargs):
    return evaluate_candidate(
        RepairCandidate(recipe=recipe, origin="discovery", rationale="Test candidate"),
        BODY,
        source=SOURCE,
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=NOW,
        fixtures=(fixture(),),
        **kwargs,
    )


def test_measured_completeness_threshold_and_opt_in():
    assert evaluate(policy=RepairPolicy(auto_promote=True)).reasons == (
        "completeness_below_threshold",
    )
    assert (
        evaluate(policy=RepairPolicy(auto_promote=True, min_completeness=0.5)).decision == "promote"
    )
    assert evaluate(policy=RepairPolicy(min_completeness=0.5)).decision == "quarantine"


def test_unassessable_consistency_does_not_pass():
    result = evaluate(
        policy=RepairPolicy(auto_promote=True, min_completeness=0.5),
        rules=(AfterObservation(kind="after_observation", field="extra"),),
    )
    assert result.indicators.verdict == "not_assessed"
    assert result.decision == "quarantine"


def test_current_success_cannot_hide_historical_regression():
    candidate = RECIPE.model_copy(update={"fields": {"title": "h3::text"}})
    result = evaluate_candidate(
        RepairCandidate(recipe=candidate, origin="discovery", rationale="Test candidate"),
        "<article><h3>Changed</h3></article>",
        source=SOURCE,
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=NOW,
        fixtures=(fixture(),),
        policy=RepairPolicy(auto_promote=True, min_completeness=0.5),
    )
    assert result.current_status == "success"
    assert result.decision == "reject"
    assert result.historical_passed == 0


def test_ci_refuses_weak_evidence_and_wrong_fixture_context():
    with pytest.raises(ConfigurationError):
        evaluate(RECIPE.model_copy(update={"require_evidence": False}))
    wrong = fixture().model_copy(
        update={"manifest": fixture().manifest.model_copy(update={"schema_fingerprint": "wrong"})}
    )
    with pytest.raises(ConfigurationError):
        evaluate_candidate(
            RepairCandidate(recipe=RECIPE, origin="discovery", rationale="Test candidate"),
            BODY,
            source=SOURCE,
            schema=Item,
            parser=ScraplingParser(),
            limits=Limits(),
            observed_at=NOW,
            fixtures=(wrong,),
        )
