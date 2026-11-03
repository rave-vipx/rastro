import json
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from rastro import JsonLdRecipe, Limits, Recipe, RecipeKey, ResourceLimitError, Source
from rastro.adapters.structured import load_json
from rastro.domain.json import MISSING, escape_token, resolve_pointer, strict_json

KEYS = st.text(max_size=12)
JSON_SCALARS = st.none() | st.booleans() | st.integers() | st.text(max_size=20)


@given(st.lists(KEYS, min_size=1, max_size=6), JSON_SCALARS)
def test_escaped_pointer_resolves_nested_keys(path, value):
    document: object = value
    for key in reversed(path):
        document = {key: document}
    pointer = "".join(f"/{escape_token(key)}" for key in path)
    assert resolve_pointer(document, pointer) == value
    assert Recipe(strategy="json", record_path="", fields={"x": pointer}).fields["x"] == pointer


@given(st.lists(JSON_SCALARS, max_size=20), st.integers(min_value=0, max_value=30))
def test_array_indexes_are_bounded(items, index):
    expected = items[index] if index < len(items) else MISSING
    assert resolve_pointer(items, f"/{index}") == expected
    assert resolve_pointer(items, f"/0{index}") is MISSING
    assert resolve_pointer(items, "/-") is MISSING


def test_root_pointer_returns_document():
    assert resolve_pointer({"a": 1}, "") == {"a": 1}


@pytest.mark.parametrize(
    "fields",
    [{"name": "name"}, {"name": "/a~2"}, {"name": "/~"}, {"name": ""}],
)
def test_json_recipe_requires_absolute_pointers(fields):
    with pytest.raises(ValidationError):
        Recipe(strategy="json", record_path="", fields=fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"record_path": None},
        {"record_path": "items"},
        {"record_path": "/a~"},
        {"record_selector": "article"},
        {"item_type": "Product"},
        {"fallbacks": (Recipe(strategy="jsonld", fields={"name": "/name"}),)},
    ],
)
def test_json_recipe_shape_is_explicit(changes):
    with pytest.raises(ValidationError):
        Recipe.model_validate(
            {"strategy": "json", "record_path": "/items", "fields": {"name": "/name"}, **changes}
        )


def test_html_recipes_cannot_mix_json_fields_or_fallbacks():
    with pytest.raises(ValidationError):
        Recipe(strategy="jsonld", fields={"name": "/name"}, record_path="/items")
    with pytest.raises(ValidationError):
        Recipe(
            strategy="jsonld",
            fields={"name": "/name"},
            fallbacks=(Recipe(strategy="json", record_path="", fields={"name": "/name"}),),
        )


def test_json_recipe_round_trips_and_reports_representation():
    recipe = Recipe(
        strategy="json",
        record_path="/data/items",
        fields={"name": "/name"},
        fallbacks=(Recipe(strategy="json", record_path="/items", fields={"name": "/title"}),),
    )
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe
    assert recipe.representation == "json"
    assert Recipe(strategy="css", record_selector="a", fields={"x": "b::text"}).representation == (
        "html"
    )


def test_legacy_recipe_payload_without_record_path_loads():
    legacy = {"strategy": "jsonld", "fields": {"name": "/name"}, "version": 1}
    loaded = Recipe.model_validate_json(json.dumps(legacy))
    assert isinstance(loaded, JsonLdRecipe) and not hasattr(loaded, "record_path")


def test_representation_isolates_memory_without_changing_html_keys(schema):
    source = Source(url="https://example.com/api")
    html_key = RecipeKey.for_source(source, schema)
    json_key = RecipeKey.for_source(source, schema, representation="json")
    assert "representation" not in html_key.canonical()
    assert '"representation":"json"' in json_key.canonical()
    assert html_key.canonical() != json_key.canonical()


@pytest.mark.parametrize(
    ("text", "limits"),
    [
        ('{"a": "' + "x" * 20 + '"}', Limits(max_json_bytes=10)),
        ("[[[[1]]]]", Limits(max_json_depth=3)),
        ("[1, 2, 3, 4]", Limits(max_json_nodes=4)),
        ("[" * 100_000 + "]" * 100_000, Limits(max_json_bytes=1_000_000)),
    ],
    # Short ids: Windows caps PYTEST_CURRENT_TEST, which embeds the test id, at 32,767 chars.
    ids=["bytes", "depth", "nodes", "recursion"],
)
def test_json_budgets_raise_explicit_errors(text, limits):
    with pytest.raises(ResourceLimitError):
        load_json(text, limits)


def test_json_budgets_accept_inputs_at_the_limit():
    assert load_json("[[[1]]]", Limits(max_json_depth=3, max_json_nodes=4)) == [[[1]]]


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', "[NaN]", "", "{", "﻿{}"])
def test_malformed_ambiguous_or_nonstandard_json_is_rejected(text):
    with pytest.raises(ValueError):
        load_json(text, Limits())


def test_invalid_pointer_validation_is_linear():
    # Regression: an ambiguous separator pattern backtracked exponentially here.
    started = time.perf_counter()
    with pytest.raises(ValidationError):
        Recipe(strategy="json", record_path="/" * 1000 + "~", fields={"name": "/name"})
    with pytest.raises(ValidationError):
        Recipe(strategy="jsonld", fields={"name": "/" * 1000 + "~"})
    assert time.perf_counter() - started < 1


@pytest.mark.parametrize(
    "text",
    ["1e999", "-1e999", "1E+400", "1e-999", "-0.1e-400", "9" * 4301, "-" + "9" * 4301],
    ids=[
        "overflow",
        "negative-overflow",
        "exponent",
        "underflow",
        "negative-underflow",
        "long-int",
        "long-negative-int",
    ],
)
def test_numbers_outside_the_exact_range_are_rejected(text):
    with pytest.raises(ValueError):
        strict_json(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("0.0", 0.0),
        ("-0.0", -0.0),
        ("0e10", 0.0),
        ("1.5e308", 1.5e308),
        ("9" * 4300, int("9" * 4300)),
    ],
    ids=["zero", "negative-zero", "zero-exponent", "large-float", "max-digit-int"],
)
def test_finite_numbers_are_preserved(text, value):
    assert strict_json(text) == value
