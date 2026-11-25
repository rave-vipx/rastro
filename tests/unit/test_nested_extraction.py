"""Nested list and object extraction in the CSS, JSON-LD, JSON and embedded JSON parsers."""

import json

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.structured import JsonParser
from rastro.domain.models import Limits, Recipe

ROWS = """
<table><tr class="show"><td class="name">Night</td><td>
<ul>
<li class="tier"><b>Standard</b><i>10</i></li>
<li class="tier"><b>Balcony</b><i>25</i></li>
<li class="tier"><b>Box</b></li>
</ul></td></tr></table>
"""
TIERS = {
    "each": ".tier",
    "members": {"tier": "b::text", "price": "i::text"},
}


def css(fields: dict[str, object]) -> Recipe:
    return Recipe.model_validate(
        {"strategy": "css", "record_selector": "tr.show", "fields": fields}
    )


def test_css_list_of_objects() -> None:
    result = ScraplingParser().parse(ROWS, css({"name": ".name::text", "tiers": TIERS}), Limits())
    candidate = result.candidates[0]
    assert candidate.values["tiers"] == [
        {"tier": "Standard", "price": "10"},
        {"tier": "Balcony", "price": "25"},
        {"tier": "Box", "price": None},
    ]
    assert candidate.values["name"] == "Night"


def test_member_missing_optional_is_null() -> None:
    candidate = ScraplingParser().parse(ROWS, css({"tiers": TIERS}), Limits()).candidates[0]
    assert candidate.values["tiers"][2]["price"] is None
    assert candidate.issues == ()


def test_css_list_of_scalars() -> None:
    mapping = {"each": ".tier", "value": "b::text"}
    candidate = ScraplingParser().parse(ROWS, css({"tiers": mapping}), Limits()).candidates[0]
    assert candidate.values["tiers"] == ["Standard", "Balcony", "Box"]


def test_evidence_per_top_level_field() -> None:
    candidate = ScraplingParser().parse(ROWS, css({"tiers": TIERS}), Limits()).candidates[0]
    assert list(candidate.locators) == ["tiers"]
    assert ".tier" in candidate.locators["tiers"]


def test_no_matching_items_leaves_the_field_missing() -> None:
    mapping = {"each": ".absent", "members": {"tier": "b::text"}}
    candidate = ScraplingParser().parse(ROWS, css({"tiers": mapping}), Limits()).candidates[0]
    assert "tiers" not in candidate.values


def test_list_max_items_truncates_with_issue() -> None:
    result = ScraplingParser().parse(
        ROWS, css({"tiers": TIERS}), Limits(max_records=2, max_json_nodes=10_000)
    )
    candidate = result.candidates[0]
    assert len(candidate.values["tiers"]) == 2
    assert [(i.code, i.field) for i in candidate.issues] == [("extraction_limit", "tiers")]


def test_ambiguous_member_is_null_with_issue() -> None:
    page = '<tr class="show"><td class="t"><b>A</b><b>B</b></td></tr>'
    mapping = {"each": ".t", "members": {"tier": "b::text"}}
    candidate = ScraplingParser().parse(page, css({"tiers": mapping}), Limits()).candidates[0]
    assert candidate.values["tiers"] == [{"tier": None}]
    assert [i.code for i in candidate.issues] == ["ambiguous_match"]


JSONLD = {
    "@type": "Event",
    "name": "Night",
    "currency": "XYZ",
    "offers": [
        {"@type": "Offer", "name": "Standard", "price": 10},
        {"@type": "Offer", "name": "Balcony"},
    ],
}


def jsonld_page(payload: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def test_jsonld_offers_wildcard() -> None:
    recipe = Recipe.model_validate(
        {
            "strategy": "jsonld",
            "item_type": "Event",
            "fields": {
                "offers": {
                    "each": "/offers/*",
                    "members": {"tier": "/name", "price": "/price", "currency": "2/currency"},
                }
            },
        }
    )
    candidate = ScraplingParser().parse(jsonld_page(JSONLD), recipe, Limits()).candidates[0]
    assert candidate.values["offers"] == [
        {"tier": "Standard", "price": 10, "currency": "XYZ"},
        {"tier": "Balcony", "price": None, "currency": "XYZ"},
    ]
    assert candidate.locators["offers"].endswith("/offers/*")


def test_jsonld_single_object_counts_as_one_item() -> None:
    payload = {**JSONLD, "offers": {"@type": "Offer", "name": "Only"}}
    recipe = Recipe.model_validate(
        {
            "strategy": "jsonld",
            "fields": {"offers": {"each": "/offers/*", "value": "/name"}},
        }
    )
    candidate = ScraplingParser().parse(jsonld_page(payload), recipe, Limits()).candidates[0]
    assert candidate.values["offers"] == ["Only"]


def test_json_recipe_nested_members() -> None:
    document = {"items": [{"id": 1, "tags": [{"n": "a"}, {"n": "b"}]}, {"id": 2}]}
    recipe = Recipe.model_validate(
        {
            "strategy": "json",
            "record_path": "/items",
            "fields": {"id": "/id", "tags": {"each": "/tags/*", "value": "/n"}},
        }
    )
    first, second = JsonParser().parse(json.dumps(document), recipe, Limits()).candidates
    assert first.values["tags"] == ["a", "b"]
    assert "tags" not in second.values
    assert first.locators["tags"] == "json#/items/0/tags/*"


def test_json_truncates_with_issue() -> None:
    document = {"items": [{"tags": [{"n": "a"}, {"n": "b"}, {"n": "c"}]}]}
    recipe = Recipe.model_validate(
        {
            "strategy": "json",
            "record_path": "/items",
            "fields": {"tags": {"each": "/tags/*", "value": "/n"}},
        }
    )
    result = JsonParser().parse(json.dumps(document), recipe, Limits(max_records=2))
    assert result.candidates[0].values["tags"] == ["a", "b"]
    assert result.candidates[0].issues[0].code == "extraction_limit"


def test_embedded_json_nested_members() -> None:
    page = (
        '<script id="d" type="application/json">'
        '{"rows": [{"offers": [{"p": 1}, {"p": 2}]}]}</script>'
    )
    recipe = Recipe.model_validate(
        {
            "strategy": "embedded_json",
            "source": "script#d::text",
            "record_path": "/rows",
            "fields": {"offers": {"each": "/offers/*", "members": {"price": "/p"}}},
        }
    )
    candidate = ScraplingParser().parse(page, recipe, Limits()).candidates[0]
    assert candidate.values["offers"] == [{"price": 1}, {"price": 2}]
    assert candidate.locators["offers"].endswith("#/rows/0/offers/*")
