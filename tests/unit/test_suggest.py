"""Example-driven recipe suggestions on synthetic layouts (INT-002, DISC-003, P-003)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel

import rastro
from rastro import (
    ConfigurationError,
    CssRecipe,
    Limits,
    Outcome,
    ResourceLimitError,
    StringField,
    SuggestionError,
)
from rastro.domain.suggest import check_examples, infer_field_type, value_matches

URL = "https://acme.example/careers"


class Offer(BaseModel):
    title: str
    salary: int


class Linked(BaseModel):
    title: str
    link: str


CARDS = """<html><body><nav><a href="/">Home</a><span>Senior roles</span></nav>
<div class="jobs">
 <article class="card job-card x1a2b"><h2 class="title">Senior Dev</h2>
   <span class="salary">120</span><a class="more" href="/jobs/1">More</a></article>
 <article class="card job-card"><h2 class="title">Junior&nbsp;Dev</h2>
   <span class="salary">80</span><a class="more" href="/jobs/2">More</a></article>
 <article class="card job-card"><h2 class="title">QA</h2>
   <span class="salary">90</span><a class="more" href="/jobs/3">More</a></article>
</div></body></html>"""


def _rows(found: rastro.Suggestion[BaseModel]) -> list[dict[str, object]]:
    return [record.data.model_dump(mode="json") for record in found.preview.records]


def test_cards_with_classes_yield_verified_recipe_and_preview():
    found = rastro.suggest(
        CARDS, Offer, examples=[{"title": "Senior Dev", "salary": "120"}], url=URL
    )
    assert isinstance(found.recipe, CssRecipe)
    assert found.recipe.record_selector == "article.card"
    assert found.preview.status is Outcome.SUCCESS
    assert [row["salary"] for row in _rows(found)] == [120, 80, 90]
    title = found.confidence["title"]
    assert title.method == "example"
    assert (title.examples_matched, title.records_with_value) == (1, 3)


def test_nbsp_and_whitespace_in_examples_are_normalized():
    found = rastro.suggest(CARDS, Offer, examples=[{"title": " Junior  Dev ", "salary": "80"}])
    assert len(found.preview.records) == 3


def test_table_rows_use_positional_cells():
    html = """<table class="jobs"><thead><tr><th>Title</th><th>Salary</th></tr></thead><tbody>
    <tr><td>Senior Dev</td><td>120</td></tr><tr><td>Junior Dev</td><td>80</td></tr>
    </tbody></table>"""
    found = rastro.suggest(html, Offer, examples=[{"title": "Junior Dev", "salary": "80"}])
    assert found.preview.status is Outcome.SUCCESS
    assert _rows(found) == [
        {"title": "Senior Dev", "salary": 120},
        {"title": "Junior Dev", "salary": 80},
    ]
    assert "nth-of-type" in found.recipe.fields["salary"]


def test_definition_lists_pick_the_matching_dd():
    html = """<main>
    <dl class="offer"><dt>Title</dt><dd>Senior Dev</dd><dt>Salary</dt><dd>120</dd></dl>
    <dl class="offer"><dt>Title</dt><dd>Analyst</dd><dt>Salary</dt><dd>95</dd></dl></main>"""
    found = rastro.suggest(html, Offer, examples=[{"title": "Senior Dev", "salary": "120"}])
    assert found.recipe.record_selector == "dl.offer"
    assert _rows(found)[1] == {"title": "Analyst", "salary": 95}


def test_generated_class_names_and_ids_never_reach_selectors():
    html = """<section id="list-77">
    <div class="css-1x2y3z" id="job-1"><div class="jsx-381"><p class="Title_x__9aZ">Senior Dev</p>
    <p class="Salary_y__3bQ">120</p></div></div>
    <div class="css-1x2y3z" id="job-2"><div class="jsx-381"><p class="Title_x__9aZ">Analyst</p>
    <p class="Salary_y__3bQ">95</p></div></div></section>"""
    found = rastro.suggest(html, Offer, examples=[{"title": "Senior Dev", "salary": "120"}])
    selectors = [found.recipe.record_selector, *found.recipe.fields.values()]
    assert not any(character.isdigit() for item in selectors for character in item.split(":")[0])
    assert not any("#" in item or "css-" in item or "__" in item for item in selectors)
    assert _rows(found)[1] == {"title": "Analyst", "salary": 95}


def test_attributes_for_urls_resolve_relative_links():
    html = """<ul><li class="item" data-id="a1"><a href="/jobs/1">Senior Dev</a></li>
    <li class="item" data-id="b2"><a href="/jobs/2">Analyst</a></li></ul>"""
    found = rastro.suggest(
        html,
        Linked,
        examples=[{"title": "Senior Dev", "link": "https://acme.example/jobs/1"}],
        url=URL,
    )
    assert found.recipe.fields["link"].endswith("::attr(href)")
    assert _rows(found)[1] == {"title": "Analyst", "link": "/jobs/2"}


def test_two_examples_are_both_reproduced():
    found = rastro.suggest(
        CARDS,
        Offer,
        examples=[{"title": "Senior Dev", "salary": "120"}, {"title": "QA", "salary": "90"}],
    )
    assert found.confidence["salary"].examples_matched == 2


def test_job_field_definitions_are_accepted_as_schema():
    fields = [StringField(name="title"), {"name": "salary", "type": "decimal"}]
    found = rastro.suggest(CARDS, fields, examples=[{"title": "QA", "salary": "90"}])
    assert found.preview.records[2].data.salary == Decimal("90")  # type: ignore[attr-defined]


def test_optional_field_without_example_is_left_unmapped_with_note():
    class WithNotes(Offer):
        notes: str | None = None

    found = rastro.suggest(CARDS, WithNotes, examples=[{"title": "QA", "salary": "90"}])
    assert "notes" not in found.recipe.fields
    assert found.confidence["notes"].method == "unmapped"
    assert found.confidence["notes"].note


def test_field_without_example_uses_exact_microdata_name():
    html = """<div class="p" itemscope><b>Lamp</b><span itemprop="sku">L-1</span></div>
    <div class="p" itemscope><b>Desk</b><span itemprop="sku">D-2</span></div>"""

    class Product(BaseModel):
        name: str
        sku: str

    found = rastro.suggest(html, Product, examples=[{"name": "Lamp"}])
    assert found.confidence["sku"].method == "microdata"
    assert _rows(found)[1] == {"name": "Desk", "sku": "D-2"}


def test_required_field_without_example_or_microdata_fails_clearly():
    with pytest.raises(SuggestionError, match="required field 'salary' has no example"):
        rastro.suggest(CARDS, Offer, examples=[{"title": "QA"}])


def test_example_not_present_names_the_missing_field():
    with pytest.raises(SuggestionError, match="example 1 'salary'"):
        rastro.suggest(CARDS, Offer, examples=[{"title": "QA", "salary": "777"}])


def test_single_record_is_ambiguous_unless_records_min_is_one():
    html = "<div class='card'><h2>Only</h2><span>5</span></div>"
    with pytest.raises(SuggestionError, match="repeats at least 2 times"):
        rastro.suggest(html, Offer, examples=[{"title": "Only", "salary": "5"}])
    found = rastro.suggest(html, Offer, examples=[{"title": "Only", "salary": "5"}], records_min=1)
    assert _rows(found) == [{"title": "Only", "salary": 5}]


def test_script_text_is_never_matched():
    html = "<script>var t='QA';</script>" + CARDS.replace("QA", "Tester")
    with pytest.raises(SuggestionError, match="not found"):
        rastro.suggest(html, Offer, examples=[{"title": "QA", "salary": "90"}])


def test_suggestion_is_deterministic():
    first = rastro.suggest(CARDS, Offer, examples=[{"title": "QA", "salary": "90"}])
    second = rastro.suggest(CARDS, Offer, examples=[{"title": "QA", "salary": "90"}])
    assert first.recipe == second.recipe
    assert first.confidence == second.confidence


def test_input_and_example_bounds_fail_closed():
    with pytest.raises(ResourceLimitError):
        rastro.suggest(CARDS, Offer, examples=[{"title": "QA"}], limits=Limits(max_html_bytes=10))
    with pytest.raises(ConfigurationError, match="not in the schema"):
        rastro.suggest(CARDS, Offer, examples=[{"nope": "QA"}])
    with pytest.raises(ConfigurationError, match="records_min"):
        rastro.suggest(CARDS, Offer, examples=[{"title": "QA"}], records_min=0)
    with pytest.raises(ConfigurationError, match="invalid job field"):
        rastro.suggest(CARDS, [{"name": "title", "type": "blob"}], examples=[{"title": "QA"}])
    with pytest.raises(SuggestionError, match="empty"):
        rastro.suggest("  ", Offer, examples=[{"title": "QA"}])


@pytest.mark.parametrize(
    "examples",
    [[], [{}], [{"title": ""}], [{"title": 5}], [{"title": "x" * 1001}]],
)
def test_check_examples_rejects_bad_input(examples):
    with pytest.raises(ConfigurationError):
        check_examples(examples)


@pytest.mark.parametrize(
    ("values", "kind"),
    [
        (["120", "80"], "integer"),
        (["120", "9.5"], "decimal"),
        (["007"], "string"),
        (["1,200"], "string"),
        (["2026-03-01"], "date"),
        (["2026-13-01"], "string"),
        (["https://a.example/x"], "url"),
        (["/jobs/1"], "string"),
        (["Senior Dev"], "string"),
    ],
)
def test_infer_field_type_is_conservative(values, kind):
    assert infer_field_type(values) == kind


def test_value_matches_rules():
    assert value_matches("120", "$120 / year")
    assert value_matches("9.50", "9.5")
    assert value_matches("https://a.example/j/1", "/j/1", "https://a.example/list")
    assert not value_matches("12", None)
    assert not value_matches("12", "13")


def test_node_budget_fails_closed():
    html = "<b>QA</b>" + "<i></i>" * 50_001
    with pytest.raises(ResourceLimitError, match="node count"):
        rastro.suggest(html, Offer, examples=[{"title": "QA"}])


def test_header_row_outside_thead_falls_back_to_partial_success():
    html = """<table><tr><th>Title</th><th>Salary</th></tr>
    <tr><td>Senior Dev</td><td>120</td></tr><tr><td>Analyst</td><td>95</td></tr></table>"""
    found = rastro.suggest(html, Offer, examples=[{"title": "Analyst", "salary": "95"}])
    assert found.preview.status is Outcome.PARTIAL_SUCCESS
    assert len(found.preview.records) == 2


class Priced(BaseModel):
    name: str
    price: Decimal


PRICED = """<ul><li class="p"><b>Teclado mecánico</b><span>59.90 EUR</span></li>
<li class="p"><b>Ratón</b><span>19.00 EUR</span></li></ul>"""


def test_type_failure_blames_only_the_field_whose_page_value_fails_the_schema():
    """B3: found on the page but not valid for the type is not 'not reproduced' (INT-003)."""
    with pytest.raises(SuggestionError) as caught:
        rastro.suggest(PRICED, Priced, examples=[{"name": "Teclado mecánico", "price": "59.90"}])
    message = str(caught.value)
    assert "'price'" in message and "'name'" not in message
    assert "Decimal" in message
    assert "9 characters" in message and "59.90" not in message
    assert "BeforeValidator" in message and "string field" in message
    assert "not found" not in message


def test_partial_result_notes_fields_whose_page_values_fail_the_schema():
    html = PRICED.replace("19.00 EUR", "n/a").replace("59.90 EUR", "59.90")
    found = rastro.suggest(html, Priced, examples=[{"name": "Teclado mecánico", "price": "59.90"}])
    assert found.status is Outcome.PARTIAL_SUCCESS
    assert "fail the schema type" in found.confidence["price"].note
    assert "schema type" not in found.confidence["name"].note
