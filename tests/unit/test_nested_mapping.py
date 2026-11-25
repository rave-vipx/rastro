"""Nested recipe definitions and bounded JSON pointer traversal."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rastro.domain.json import MISSING, resolve_pointer
from rastro.domain.recipes import Recipe
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.domain.sources import ConfigurationError


def css(mapping: object) -> Recipe:
    return Recipe.model_validate(
        {"strategy": "css", "record_selector": "article", "fields": {"offers": mapping}}
    )


def test_nested_mapping_requires_members_xor_value() -> None:
    for mapping in (
        {"each": ".offer", "members": {"price": "b::text"}},
        {"each": ".offer", "value": "::text"},
    ):
        recipe = css(mapping)
        assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe
    for mapping in (
        {"each": ".offer"},
        {"each": ".offer", "members": {"price": "b::text"}, "value": "::text"},
    ):
        with pytest.raises(ValidationError):
            css(mapping)


def test_pointer_wildcard_resolves_array() -> None:
    payload = {"offers": [{"price": 1}, {"price": 2}, {"price": 3}]}
    assert resolve_pointer(payload, "/offers/*/price") == [1, 2, 3]


def test_relative_pointer_reads_parent() -> None:
    parent = {"currency": "XYZ", "offers": [{"price": 1}]}
    assert resolve_pointer(parent["offers"], "1/currency", parents=(parent,)) == "XYZ"


def test_old_recipe_json_still_validates() -> None:
    recipe = Recipe.model_validate_json(Path("examples/product.recipe.json").read_text())
    assert recipe.fields["price"] == "/offers/price"


def test_cost_policy_applies_to_each() -> None:
    recipe = css({"each": ":has(a)", "value": "::text"})
    with pytest.raises(ConfigurationError, match=":has"):
        check_untrusted_recipe(recipe)


@pytest.mark.parametrize("part", ["members", "value"])
def test_cost_policy_applies_to_nested_values(part: str) -> None:
    mapping = {"price": ":has(a)::text"} if part == "members" else ":has(a)::text"
    with pytest.raises(ConfigurationError, match=":has"):
        check_untrusted_recipe(css({"each": ".offer", part: mapping}))


@pytest.mark.parametrize("each", ["/offers", "/offers/*/*", "1/offers/*"])
def test_json_each_requires_one_absolute_wildcard(each: str) -> None:
    with pytest.raises(ValidationError):
        Recipe.model_validate(
            {
                "strategy": "json",
                "record_path": "",
                "fields": {"offers": {"each": each, "value": "/price"}},
            }
        )


def test_json_nested_mapping_accepts_relative_member() -> None:
    recipe = Recipe.model_validate(
        {
            "strategy": "json",
            "record_path": "",
            "fields": {
                "offers": {
                    "each": "/offers/*",
                    "members": {"price": "/price", "currency": "2/currency"},
                }
            },
        }
    )
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe


def test_wildcard_budget_and_missing_values_are_explicit() -> None:
    with pytest.raises(ConfigurationError):
        resolve_pointer([1, 2, 3], "/*", max_records=2)
    assert resolve_pointer([{}, {"price": 1}], "/*/price") is MISSING
    assert resolve_pointer({}, "1/currency") is MISSING
    assert resolve_pointer({}, "invalid") is MISSING
    assert resolve_pointer([], "/*") == []


@pytest.mark.parametrize("each", ["", " ", ".offer::text", "expand:.offer", "document:.offer"])
def test_css_each_is_a_plain_scope_selector(each: str) -> None:
    with pytest.raises(ValidationError):
        css({"each": each, "value": "::text"})


@pytest.mark.parametrize(
    "settings",
    [
        {"strategy": "json", "record_path": ""},
        {"strategy": "jsonld"},
        {"strategy": "embedded_json", "source": "script::text", "record_path": ""},
    ],
)
def test_nested_pointer_mapping_in_each_json_strategy(settings: dict[str, str]) -> None:
    recipe = Recipe.model_validate(
        {**settings, "fields": {"prices": {"each": "/offers/*", "value": "/price"}}}
    )
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe


@pytest.mark.parametrize(
    "mapping",
    [
        {"each": ".offer", "members": {"": "::text"}},
        {"each": ".offer", "members": {"price": "b"}},
        {"each": ".offer", "value": "b"},
        {"each": ".offer", "members": {"price": {"each": "b", "value": "::text"}}},
        {"each": ".offer", "members": {str(i): "::text" for i in range(33)}},
    ],
)
def test_nested_members_are_bounded_scalar_expressions(mapping: object) -> None:
    with pytest.raises(ValidationError):
        css(mapping)


def test_pointer_context_and_escaping_preserve_scalar_behavior() -> None:
    root = {"currency": "XYZ"}
    parent = [{"price": 1}]
    record = parent[0]
    assert resolve_pointer(record, "2/currency", parents=(root, parent)) == "XYZ"
    assert resolve_pointer(record, "0/price") == 1
    assert resolve_pointer(record, "0") is record
    assert resolve_pointer(record, "3/currency", parents=(root, parent)) is MISSING
    assert resolve_pointer({"a/b": {"~key": 2}}, "/a~1b/~0key") == 2
    assert resolve_pointer({"*": 3}, "/*") == 3
    assert resolve_pointer([1, 2], "/*", max_records=2) == [1, 2]
    assert resolve_pointer([[1], [2]], "/*/*", max_records=2) == [1, 2]
    with pytest.raises(ConfigurationError):
        resolve_pointer([[1, 2], [3]], "/*/*", max_records=2)


@pytest.mark.parametrize("pointer", ["/01", "/-1", "/9", "/" + "9" * 5000, "/~2", "01/x"])
def test_invalid_pointer_returns_missing(pointer: str) -> None:
    assert resolve_pointer([1], pointer) is MISSING
