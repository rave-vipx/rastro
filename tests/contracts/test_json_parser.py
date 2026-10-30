import json

import pytest

from rastro import ConfigurationError, Limits, Recipe, ResourceLimitError
from rastro.adapters.structured import JsonDiscovery, JsonParser

ITEMS = json.dumps(
    {
        "meta": {"name": "catalogue", "count": 2},
        "data": {
            "items": [
                {"name": "Notebook", "pricing": {"price": "12.50"}},
                {"name": "Pen", "pricing": {"price": "1.20"}, "tags": [{"name": "ink"}]},
            ]
        },
    }
)
RECIPE = Recipe(
    strategy="json",
    record_path="/data/items",
    fields={"name": "/name", "price": "/pricing/price"},
)


def parse(text, recipe=RECIPE, limits=None):
    return JsonParser().parse(text, recipe, limits or Limits())


def discover(text, fields=("name", "price"), limits=None, maximum=8):
    return JsonDiscovery().discover(text, fields, limits or Limits(), max_candidates=maximum)


def test_array_records_use_relative_pointers_with_absolute_locators():
    parsed = parse(ITEMS)
    assert [c.values for c in parsed.candidates] == [
        {"name": "Notebook", "price": "12.50"},
        {"name": "Pen", "price": "1.20"},
    ]
    assert parsed.candidates[1].locators == {
        "name": "json#/data/items/1/name",
        "price": "json#/data/items/1/pricing/price",
    }


def test_object_record_path_and_root_record():
    single = parse(ITEMS, RECIPE.model_copy(update={"record_path": "/data/items/0"}))
    assert single.candidates[0].locators["name"] == "json#/data/items/0/name"
    flat = Recipe(strategy="json", record_path="", fields={"name": "/name", "price": "/price"})
    root = parse('{"name": "A", "price": 1}', flat)
    assert root.candidates[0].values == {"name": "A", "price": 1}
    assert root.candidates[0].locators["name"] == "json#/name"


def test_escaped_keys_and_missing_fields():
    recipe = Recipe(strategy="json", record_path="/a~1b", fields={"name": "/x~0y", "price": "/p"})
    parsed = parse('{"a/b": [{"x~y": "N"}]}', recipe)
    assert parsed.candidates[0].values == {"name": "N"}
    assert parsed.candidates[0].locators == {"name": "json#/a~1b/0/x~0y"}


@pytest.mark.parametrize(
    ("text", "path", "code"),
    [
        ('{"a": 1, "a": 2}', "/data/items", "invalid_json"),
        ("[Infinity]", "/data/items", "invalid_json"),
        ("", "/data/items", "invalid_json"),
        ('{"data": {}}', "/data/items", "record_path_not_found"),
        ('{"data": {"items": "x"}}', "/data/items", "invalid_record_path"),
    ],
)
def test_document_level_failures_are_explicit_issues(text, path, code):
    parsed = parse(text, RECIPE.model_copy(update={"record_path": path}))
    assert parsed.candidates == ()
    assert [issue.code for issue in parsed.issues] == [code]


def test_non_object_record_is_rejected_individually():
    parsed = parse('{"data": {"items": [{"name": "A"}, 3]}}')
    assert parsed.candidates[0].values == {"name": "A"}
    assert parsed.candidates[1].issues[0].code == "invalid_record"
    assert parsed.candidates[1].issues[0].record_index == 1


def test_record_limit_and_strategy_mismatch():
    with pytest.raises(ResourceLimitError):
        parse(ITEMS, limits=Limits(max_records=1))
    with pytest.raises(ConfigurationError):
        JsonParser().parse(ITEMS, Recipe(strategy="jsonld", fields={"name": "/n"}), Limits())


def test_discovery_prefers_most_complete_arrays_then_document_order():
    candidates = discover(ITEMS)
    assert candidates[0] == RECIPE
    assert [c.record_path for c in candidates] == ["/data/items", "", "/data/items/1/tags"]
    assert candidates[1].fields == {"name": "/meta/name"}


def test_discovery_drops_ambiguous_and_inconsistent_field_names():
    text = json.dumps(
        {
            "items": [
                {"name": "A", "price": 1, "brand": {"name": "B"}},
                {"name": "C", "info": {"price": 2}},
            ]
        }
    )
    assert discover(text) == ()


def test_discovery_is_bounded_and_never_raises_for_invalid_json():
    assert discover('{"name": 1, "name": 2}') == ()
    assert discover('[{"name": "A"}, 1]') == ()
    assert len(discover(ITEMS, maximum=1)) == 1
    with pytest.raises(ResourceLimitError):
        discover(ITEMS, limits=Limits(max_json_nodes=3))
    many = json.dumps({"items": [{"name": str(i)} for i in range(5)]})
    assert all(c.record_path != "/items" for c in discover(many, limits=Limits(max_records=4)))


def test_discovery_skips_mappings_that_exceed_recipe_bounds():
    long_key = "k" * 1100
    assert discover(json.dumps([{long_key: {"name": "A"}}])) == ()
