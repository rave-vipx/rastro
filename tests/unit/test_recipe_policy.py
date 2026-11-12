"""REPAIR-002/004/005: thresholds and generated regressions use measured evidence."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro.adapters.scrapling import ScraplingParser
from rastro.application.extraction import extract_document
from rastro.application.recipe_ci import _decide, evaluate_candidate
from rastro.domain.consistency import AfterObservation, ConsistencyReport
from rastro.domain.models import ConfigurationError, CssRecipe, Limits, Outcome, Source
from rastro.domain.recipe_compare import RegressionCheck, RepairPolicy
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


def test_optional_field_absent_in_success_sample_does_not_lower_completeness():
    """QUAL-006: an optional field the success sample never filled is not counted."""
    result = evaluate(policy=RepairPolicy(auto_promote=True))
    assert result.indicators.completeness == 1.0
    assert result.decision == "promote"
    assert evaluate(policy=RepairPolicy()).reasons == ("automatic_promotion_disabled",)


def test_optional_field_filled_in_success_sample_still_gates_completeness():
    """QUAL-006: losing an optional value the success sample had stays below threshold."""
    body = "<article><h2>Hello</h2><p>More</p></article>"
    sampled = CssRecipe(
        record_selector="article",
        fields={"title": "h2::text", "extra": "p::text"},
        require_evidence=True,
    )
    result = extract_document(
        body, source=SOURCE, schema=Item, recipe=sampled, parser=ScraplingParser(), limits=Limits()
    )
    sample = build_fixture(
        body, source=SOURCE, schema=Item, recipe=sampled, result=result, synthetic_count=0
    )
    evaluation = evaluate_candidate(
        RepairCandidate(recipe=sampled, origin="discovery", rationale="Current page lost extra"),
        BODY,
        source=SOURCE,
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=NOW,
        fixtures=(sample,),
        policy=RepairPolicy(auto_promote=True),
    )
    assert evaluation.indicators.completeness == 0.5
    assert "completeness_below_threshold" in evaluation.reasons


def test_unassessable_consistency_does_not_pass():
    result = evaluate(
        policy=RepairPolicy(auto_promote=True, min_completeness=0.5),
        rules=(AfterObservation(kind="after_observation", field="extra"),),
    )
    assert result.indicators.verdict == "not_assessed"
    assert result.decision == "quarantine"


def test_consistent_candidate_below_semantic_confidence_threshold_is_quarantined():
    """QUAL-005/006, REPAIR-004: consistent does not imply sufficient confidence."""
    # Current rules give consistent runs confidence 1; exercise the CI gate's graded contract.
    indicators = ConsistencyReport(
        records=4,
        completeness=1,
        structural_validity=1,
        verdict="consistent",
        semantic_confidence=0.75,
        results=(),
    )
    decision, reasons = _decide(
        RepairCandidate(recipe=RECIPE, origin="discovery", rationale="Test candidate"),
        Outcome.SUCCESS,
        4,
        indicators,
        (RegressionCheck(name="historical_fixture_0", passed=True),),
        (RegressionCheck(name="nonempty_validated_records", passed=True),),
        True,
        RepairPolicy(auto_promote=True, min_semantic_confidence=0.9),
    )
    assert decision == "quarantine"
    assert reasons == ("semantic_threshold_not_met",)


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
