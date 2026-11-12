"""Synthetic container repairs retain history and ambiguity gates (REPAIR-001, SCR-002)."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro import CssRecipe, Limits, Outcome, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.application.extraction import extract_document
from rastro.application.recipe_ci import evaluate_candidate
from rastro.domain.recipe_compare import RepairPolicy
from rastro.mocklab.fixtures import build_fixture


class Event(BaseModel):
    title: str
    venue: str


SOURCE = Source(url="https://example.test/events")
RECIPE = CssRecipe(
    record_selector="article.dance", fields={"title": "h2.title::text", "venue": "p.venue::text"}
)
BEFORE = (
    '<html><main><article class="dance"><h2 class="title">Synthetic dance</h2>'
    '<p class="venue">Synthetic hall</p></article></main></html>'
)
AFTER = BEFORE.replace('class="dance"', 'class="performance"')


def extract(html, recipe=RECIPE):
    return extract_document(
        html, source=SOURCE, schema=Event, recipe=recipe, parser=ScraplingParser(), limits=Limits()
    )


def snapshot():
    return ScraplingRelocation().capture(
        BEFORE, source=SOURCE, recipe=RECIPE, result=extract(BEFORE), limits=Limits()
    )


def candidate_for(html):
    (candidate,) = ScraplingRelocation().relocate(html, RECIPE, snapshot(), Limits())
    return candidate


def evaluate(candidate, html):
    fixture = build_fixture(
        BEFORE,
        source=SOURCE,
        schema=Event,
        recipe=RECIPE,
        result=extract(BEFORE),
        synthetic_count=0,
    )
    return evaluate_candidate(
        candidate,
        html,
        source=SOURCE,
        schema=Event,
        parser=ScraplingParser(),
        limits=Limits(),
        observed_at=datetime(2026, 11, 8, tzinfo=UTC),
        fixtures=(fixture,),
        policy=RepairPolicy(auto_promote=True),
    )


def test_container_class_rename_yields_union_candidate_that_passes_history():
    candidate = candidate_for(AFTER)
    assert candidate.recipe.record_selector == "article.dance, main > article.performance"
    assert candidate.recipe.require_evidence
    assert candidate.evidence[0].match_count == 1
    assert not candidate.ambiguous
    verdict = evaluate(candidate, AFTER)
    assert verdict.historical_passed == 1
    assert verdict.decision == "promote"
    assert candidate.evidence[0].field == "record_selector"
    assert candidate.evidence[0].match_count == 1


def test_container_rename_plus_field_class_rename_fixed_in_one_candidate():
    changed = AFTER.replace('class="title"', 'class="renamed"')
    candidate = candidate_for(changed)
    assert candidate.recipe.fields["title"] != RECIPE.fields["title"]
    assert evaluate(candidate, changed).decision == "promote"
    assert extract(changed, candidate.recipe).records[0].data == extract(BEFORE).records[0].data


def test_wrapper_inserted_around_cards_still_relocates():
    changed = AFTER.replace("<main>", "<main><section>").replace("</main>", "</section></main>")
    candidate = candidate_for(changed)
    assert evaluate(candidate, changed).decision == "promote"


def test_container_relocation_never_matches_unrelated_promo_cards():
    changed = AFTER.replace(
        "</main>", '<aside class="performance"><h2>Sale banner</h2><a>Buy</a></aside></main>'
    )
    candidate = candidate_for(changed)
    result = extract(changed, candidate.recipe)
    assert result.status == Outcome.SUCCESS
    assert len(result.records) == 1
    assert result.records[0].data.title == "Synthetic dance"
    assert evaluate(candidate, changed).decision == "promote"


def test_homogeneous_tied_containers_are_judged_by_recipe_ci():
    card = AFTER.split("<main>")[1].split("</main>")[0]
    changed = AFTER.replace("</main>", card + "</main>")
    candidate = candidate_for(changed)
    assert candidate.evidence[0].match_count == 2
    assert len(extract(changed, candidate.recipe).records) == 2
    assert not candidate.ambiguous
    assert evaluate(candidate, changed).decision == "promote"


def test_classless_container_uses_shared_parent_path():
    changed = AFTER.replace(' class="performance"', "")
    candidate = candidate_for(changed)
    assert "main" in candidate.recipe.record_selector
    assert evaluate(candidate, changed).decision == "promote"


def test_relocated_record_count_is_bounded():
    from rastro import ResourceLimitError

    card = AFTER.split("<main>")[1].split("</main>")[0]
    changed = AFTER.replace("</main>", card + "</main>")
    with pytest.raises(ResourceLimitError, match="max_records"):
        ScraplingRelocation().relocate(changed, RECIPE, snapshot(), Limits(max_records=1))


@pytest.mark.parametrize(
    "classes", ["performance featured", "1-card", "-2-card", "card:promo,aside"]
)
def test_container_classes_are_literal_css_tokens(classes):
    changed = AFTER.replace('class="performance"', f'class="{classes}"').replace(
        "</main>", '<aside><h2>Wrong</h2><p class="venue">Wrong</p></aside></main>'
    )
    candidate = candidate_for(changed)
    assert evaluate(candidate, changed).decision == "promote"
    assert len(extract(changed, candidate.recipe).records) == 1


def test_container_relocation_does_not_log_or_persist_unmasked_content(caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        captured = snapshot()
        candidate_for(AFTER)
    for text in ("Synthetic dance", "Synthetic hall", "performance", "venue"):
        assert text not in caplog.text
    for text in ("Synthetic dance", "Synthetic hall", "performance"):
        assert text not in captured.model_dump_json()


def test_classless_homogeneous_containers_share_parent_path():
    changed = AFTER.replace(' class="performance"', "")
    card = changed.split("<main>")[1].split("</main>")[0]
    changed = changed.replace("</main>", card + "</main>")
    candidate = candidate_for(changed)
    assert "main" in candidate.recipe.record_selector
    assert len(extract(changed, candidate.recipe).records) == 2
    assert not candidate.ambiguous
    assert evaluate(candidate, changed).decision == "promote"


def test_mismatched_container_tag_cannot_become_a_candidate():
    changed = AFTER.replace("article", "aside")
    assert ScraplingRelocation().relocate(changed, RECIPE, snapshot(), Limits()) == ()


def test_heterogeneous_containers_remain_ambiguous():
    promo = (
        '<article class="performance"><h2 class="title">Synthetic promo</h2>'
        "<a>Buy something</a></article>"
    )
    changed = AFTER.replace("</main>", promo + "</main>")
    candidate = candidate_for(changed)
    assert candidate.evidence[0].match_count == 2
    assert candidate.ambiguous
    # The promo card has no matching fields, so the candidate fails validation: rejected,
    # never activated. Ambiguity alone (valid fields) quarantines.
    evaluation = evaluate(candidate, changed)
    assert evaluation.decision == "reject"
    assert evaluation.blocking


def test_container_evidence_fits_with_one_hundred_observed_fields():
    from pydantic import create_model

    fields = {f"field{i}": "h2.title::text" for i in range(100)}
    schema = create_model("HundredFields", **dict.fromkeys(fields, (str, ...)))
    recipe = CssRecipe(record_selector="article.dance", fields=fields)
    result = extract_document(
        BEFORE,
        source=SOURCE,
        schema=schema,
        recipe=recipe,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    adapter = ScraplingRelocation()
    captured = adapter.capture(BEFORE, source=SOURCE, recipe=recipe, result=result, limits=Limits())
    (candidate,) = adapter.relocate(AFTER, recipe, captured, Limits())
    assert len(candidate.evidence) == 101
    assert not candidate.ambiguous
