"""N16: multi-type JSON-LD and JSON embedded in HTML attributes or scripts (REC-*, PARSE-*)."""

import html
import json

import pytest

from rastro.adapters.scrapling import ScraplingParser
from rastro.domain.models import (
    ConfigurationError,
    EmbeddedJsonRecipe,
    JsonLdRecipe,
    Limits,
    Recipe,
    ResourceLimitError,
)
from rastro.domain.selector_policy import check_untrusted_recipe

PARSER = ScraplingParser()


def _ld(*blocks: object) -> str:
    return "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks)


def _names(doc: str, recipe) -> list[object]:
    return [c.values.get("name") for c in PARSER.parse(doc, recipe, Limits()).candidates]


def test_item_type_tuple_matches_any_type_and_single_does_not() -> None:
    page = _ld({"@type": "MusicEvent", "name": "Gig"}, {"@type": ["Thing", "Event"], "name": "B"})
    both = JsonLdRecipe(item_type=("Event", "MusicEvent"), fields={"name": "/name"})
    assert _names(page, both) == ["Gig", "B"]
    assert _names(page, JsonLdRecipe(item_type="Event", fields={"name": "/name"})) == ["B"]


def test_graph_and_top_level_array() -> None:
    graph = _ld({"@graph": [{"@type": "DanceEvent", "name": "G"}, {"@type": "Org", "name": "O"}]})
    array = _ld([{"@type": "Event", "name": "A"}, {"@type": "Event", "name": "B"}])
    recipe = JsonLdRecipe(item_type=("Event", "DanceEvent"), fields={"name": "/name"})
    assert _names(graph, recipe) == ["G"]
    assert _names(array, recipe) == ["A", "B"]


def test_unhashable_type_value_is_skipped() -> None:
    page = _ld({"@type": [{"x": 1}], "name": "Odd"})
    assert _names(page, JsonLdRecipe(item_type=("Event",), fields={"name": "/name"})) == []


def test_single_string_item_type_serializes_unchanged() -> None:
    recipe = JsonLdRecipe(item_type="Product", fields={"name": "/name"})
    assert recipe.model_dump(mode="json", exclude_defaults=True)["item_type"] == "Product"
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe
    many = JsonLdRecipe(item_type=("A", "B"), fields={"name": "/name"})
    assert Recipe.model_validate_json(many.model_dump_json()) == many


@pytest.mark.parametrize("value", [(), tuple("abcdefghi"), ("",), ("x" * 257,)])
def test_item_type_bounds(value) -> None:
    with pytest.raises(ValueError):
        JsonLdRecipe(item_type=value, fields={"name": "/name"})


RECORDS = {"state": {"items": [{"title": "One", "id": 1}, {"title": "Two", "id": 2}]}}


def _recipe(source: str, **extra) -> EmbeddedJsonRecipe:
    return EmbeddedJsonRecipe(
        source=source, record_path="/state/items", fields={"name": "/title", "id": "/id"}, **extra
    )


def test_gxstate_attribute_with_escaped_json() -> None:
    value = html.escape(json.dumps(RECORDS), quote=True)
    page = f'<form><input type="hidden" name="GXState" value="{value}"></form>'
    parsed = PARSER.parse(page, _recipe("input[name=GXState]::attr(value)"), Limits())
    assert [c.values["name"] for c in parsed.candidates] == ["One", "Two"]
    assert "embedded_json" in parsed.candidates[0].locators["name"]


def test_next_data_script() -> None:
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(RECORDS)}</script>'
    parsed = PARSER.parse(page, _recipe("script#__NEXT_DATA__::text"), Limits())
    assert [c.values["id"] for c in parsed.candidates] == [1, 2]


def test_zero_missing_attribute_and_two_nodes_fail_explicitly() -> None:
    recipe = _recipe("input[name=GXState]::attr(value)")
    assert PARSER.parse("<p>x</p>", recipe, Limits()).issues[0].code == "embedded_json_not_found"
    no_attr = '<input name="GXState">'
    assert PARSER.parse(no_attr, recipe, Limits()).issues[0].code == "embedded_json_not_found"
    two = '<input name="GXState" value="{}"><input name="GXState" value="{}">'
    parsed = PARSER.parse(two, recipe, Limits())
    assert parsed.issues[0].code == "ambiguous_match" and not parsed.candidates


def test_invalid_json_and_missing_path_fail() -> None:
    recipe = _recipe("script#d::text")
    bad = PARSER.parse('<script id="d">{nope</script>', recipe, Limits())
    assert bad.issues[0].code == "invalid_json" and not bad.candidates
    other = PARSER.parse('<script id="d">{"a": 1}</script>', recipe, Limits())
    assert other.issues[0].code == "record_path_not_found"


def test_too_deep_json_is_a_typed_failure() -> None:
    deep = "[" * 40 + "]" * 40
    with pytest.raises(ResourceLimitError):
        PARSER.parse(f'<script id="d">{deep}</script>', _recipe("script#d::text"), Limits())


def test_source_validation_and_untrusted_policy() -> None:
    with pytest.raises(ValueError):
        _recipe("script#d")  # no ::text or ::attr
    with pytest.raises(ConfigurationError):
        _recipe("script#d::text", pagination={"next": "/x"})
    with pytest.raises(ConfigurationError):
        check_untrusted_recipe(_recipe("p ~ p ~ p::text"))
    check_untrusted_recipe(_recipe("script#d::text"))


def test_union_and_html_fallbacks() -> None:
    fallback = JsonLdRecipe(fields={"name": "/name"})
    data = _recipe("script#d::text").model_dump(mode="json")
    data["fallbacks"] = [fallback.model_dump(mode="json")]
    assert isinstance(Recipe.model_validate(data), EmbeddedJsonRecipe)
