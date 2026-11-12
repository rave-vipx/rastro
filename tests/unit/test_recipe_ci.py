"""REPAIR-002/004/005: measured CI gates, with no serving side effects."""

from datetime import UTC, datetime

from pydantic import BaseModel

from rastro.adapters.scrapling import ScraplingParser
from rastro.application.recipe_ci import evaluate_candidate
from rastro.domain.models import CssRecipe, Limits, Source
from rastro.domain.recipe_compare import RecipeComparison, RepairPolicy
from rastro.domain.repair import RepairCandidate, RepairEvidence


class Item(BaseModel):
    title: str


def test_missing_history_quarantines_even_valid_current_evidence():
    candidate = RepairCandidate(
        recipe=CssRecipe(
            record_selector="article", require_evidence=True, fields={"title": "h2::text"}
        ),
        origin="discovery",
        rationale="Observed mapping",
    )
    evaluation = evaluate_candidate(
        candidate,
        "<article><h2>Hello</h2></article>",
        source=Source(url="https://example.com/"),
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=datetime(2026, 11, 7, tzinfo=UTC),
        policy=RepairPolicy(auto_promote=True),
    )
    assert evaluation.decision == "quarantine"
    assert evaluation.current_status == "success"
    assert evaluation.historical_fixtures == 0


def test_ambiguous_evaluation_is_quarantined():
    candidate = RepairCandidate(
        recipe=CssRecipe(
            record_selector="article", require_evidence=True, fields={"title": "h2::text"}
        ),
        origin="scrapling_relocation",
        rationale="Tied nodes",
        evidence=(RepairEvidence(field="title", match_count=2),),
    )
    evaluation = evaluate_candidate(
        candidate,
        "<article><h2>A</h2><h2>B</h2></article>",
        source=Source(url="https://example.com/"),
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=datetime(2026, 11, 7, tzinfo=UTC),
    )
    # REPAIR-005: ambiguity never hides a hard failure; the evaluator rejects first.
    assert evaluation.decision == "reject"


def test_ambiguous_but_valid_evaluation_is_quarantined():
    candidate = RepairCandidate(
        recipe=CssRecipe(
            record_selector="article", require_evidence=True, fields={"title": "h2::text"}
        ),
        origin="scrapling_relocation",
        rationale="Tied nodes",
        evidence=(RepairEvidence(field="title", match_count=2),),
    )
    evaluation = evaluate_candidate(
        candidate,
        "<article><h2>A</h2></article>",
        source=Source(url="https://example.com/"),
        schema=Item,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=datetime(2026, 11, 7, tzinfo=UTC),
        policy=RepairPolicy(auto_promote=True),
    )
    assert evaluation.decision == "quarantine"
    assert "ambiguous_evidence" in evaluation.reasons


def test_recipe_comparison_is_machine_readable():
    old = CssRecipe(record_selector="article", require_evidence=True, fields={"title": "h2::text"})
    new = old.model_copy(update={"version": 2, "fields": {"title": "h3::text"}})
    comparison = RecipeComparison.between(old, new)
    assert comparison.schema_version == 1
    assert comparison.changed_fields == ("title",)
    assert RecipeComparison.model_validate_json(comparison.model_dump_json()) == comparison


def test_job_rules_are_additive_and_reject_undeclared_fields():
    import pytest

    from rastro.domain.jobs import parse_job

    raw = """format_version=1
[[sources]]
path="a.html"
[[fields]]
name="title"
type="string"
[recipe]
strategy="css"
record_selector="article"
[recipe.fields]
title="h2::text"
"""
    job = parse_job(raw, format="toml")
    assert "consistency" not in job.redacted_dump()
    assert "repair" not in job.redacted_dump()
    assert "consistency" not in job.model_dump()
    assert "repair" not in job.model_dump()
    extended = parse_job(
        raw
        + '[[consistency]]\nkind="distinct_ratio"\nfield="title"\n[repair]\nauto_promote=true\n',
        format="toml",
    )
    assert extended.consistency[0].field == "title"
    assert extended.repair.auto_promote
    with pytest.raises(ValueError):
        parse_job(raw + '[[consistency]]\nkind="distinct_ratio"\nfield="missing"\n', format="toml")


def test_recipe_digest_survives_sqlite_mapping_order():
    from rastro.domain.repair import recipe_digest

    first = CssRecipe(record_selector="article", fields={"z": "h2::text", "a": "p::text"})
    reordered = first.model_copy(update={"fields": {"a": "p::text", "z": "h2::text"}})
    assert recipe_digest(first) == recipe_digest(reordered)
