import json

import pytest
from pydantic import ValidationError

from rastro import CssRecipe, InferencePolicy, JsonLdRecipe, ModelRequest, RecipeProposal
from rastro.domain.intelligence import CssProposal, JsonLdProposal, proposal_schema


@pytest.mark.parametrize(
    "config",
    [
        {"max_calls": 0},
        {"max_calls": 5},
        {"max_repair_attempts": -1},
        {"max_repair_attempts": 4},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"max_context_bytes": 0},
        {"max_response_bytes": 0},
        {"max_concurrency": 0},
        {"max_concurrency": 17},
        {"max_output_tokens": 0},
        {"context_tokens": 0},
        {"max_discovery_candidates": 0},
        {"unknown": 1},
    ],
)
def test_inference_limits_are_validated(config):
    with pytest.raises(ValidationError):
        InferencePolicy(**config)


def test_context_budget_includes_instructions_schema_and_escaped_data():
    request = ModelRequest(system="trusted", content='Unicode: 😀 "quoted"')
    assert request.context_bytes > len(request.model_dump_json().encode())
    assert request.context_bytes > 500
    assert request.context_bytes > len(json.dumps(proposal_schema()))


def test_proposal_cannot_choose_runtime_evidence_policy():
    with pytest.raises(ValidationError):
        RecipeProposal(strategy="jsonld", fields={"name": "/name"}, require_evidence=False)


def test_proposal_schema_encodes_strategy_specific_recipe_rules():
    # Constrained decoding cannot emit a CSS recipe without its record selector.
    variants = {
        variant["properties"]["strategy"]["const"]: variant
        for variant in proposal_schema()["anyOf"]
    }
    css, jsonld = variants.pop("css"), variants.pop("jsonld")
    assert not variants
    assert set(css["required"]) == {"strategy", "fields", "record_selector"}
    assert set(css["properties"]) == {"strategy", "fields", "record_selector"}
    assert set(jsonld["required"]) == {"strategy", "fields"}
    assert set(jsonld["properties"]) == {"strategy", "fields", "item_type"}
    assert css["additionalProperties"] is False and jsonld["additionalProperties"] is False
    RecipeProposal(strategy="css", fields={"name": "h2::text"}, record_selector="article").recipe()
    RecipeProposal(strategy="jsonld", fields={"name": "/name"}).recipe()


# The constrained-decoding schema 0.1.0 sent to models, minus each variant's title.
_V0_1_PROPOSAL_SCHEMA = {
    "anyOf": [
        {
            "additionalProperties": False,
            "properties": {
                "strategy": {"const": "css", "title": "Strategy", "type": "string"},
                "fields": {
                    "additionalProperties": {"type": "string"},
                    "maxProperties": 100,
                    "minProperties": 1,
                    "title": "Fields",
                    "type": "object",
                },
                "record_selector": {
                    "maxLength": 1024,
                    "minLength": 1,
                    "title": "Record Selector",
                    "type": "string",
                },
            },
            "required": ["strategy", "fields", "record_selector"],
            "type": "object",
        },
        {
            "additionalProperties": False,
            "properties": {
                "strategy": {"const": "jsonld", "title": "Strategy", "type": "string"},
                "fields": {
                    "additionalProperties": {"type": "string"},
                    "maxProperties": 100,
                    "minProperties": 1,
                    "title": "Fields",
                    "type": "object",
                },
                "item_type": {
                    "anyOf": [
                        {"maxLength": 256, "minLength": 1, "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "title": "Item Type",
                },
            },
            "required": ["strategy", "fields"],
            "type": "object",
        },
    ]
}


def test_proposal_schema_is_derived_from_html_recipe_variants_and_unchanged():
    schema = proposal_schema()
    titles = [variant.pop("title") for variant in schema["anyOf"]]
    assert titles == ["CssProposal", "JsonLdProposal"]
    assert schema == _V0_1_PROPOSAL_SCHEMA
    # Selector constraints come from the recipe variants, not a parallel definition.
    for proposal, recipe, selector in (
        (CssProposal, CssRecipe, "record_selector"),
        (JsonLdProposal, JsonLdRecipe, "item_type"),
    ):
        assert repr(proposal.model_fields[selector]) == repr(recipe.model_fields[selector])
        assert repr(proposal.model_fields["fields"]) == repr(recipe.model_fields["fields"])


@pytest.mark.parametrize(
    ("data", "variant"),
    [
        ({"strategy": "css", "fields": {"name": "h2::text"}, "record_selector": "a"}, CssRecipe),
        ({"strategy": "jsonld", "fields": {"name": "/name"}, "item_type": "P"}, JsonLdRecipe),
        # Null selectors of the other strategy were accepted by the 0.1.0 proposal model.
        (
            {"strategy": "css", "fields": {"name": "h2::text"}, "record_selector": "a"}
            | {"item_type": None},
            CssRecipe,
        ),
        (
            {"strategy": "jsonld", "fields": {"name": "/name"}, "record_selector": None},
            JsonLdRecipe,
        ),
    ],
)
def test_strict_proposals_select_a_variant_and_become_recipes(data, variant):
    proposal = RecipeProposal.model_validate(data, strict=True)
    assert isinstance(proposal, RecipeProposal)
    assert type(proposal.recipe()) is variant


@pytest.mark.parametrize(
    "data",
    [
        {"strategy": "json", "fields": {"name": "/name"}, "record_path": ""},
        {"strategy": "css", "fields": {"name": "h2::text"}},
        {"strategy": "css", "fields": {"name": "h2::text"}, "record_selector": "a", "version": 2},
        {"strategy": "jsonld", "fields": {"name": "/name"}, "fallbacks": []},
        {"strategy": "jsonld", "fields": {"name": "/name"}, "record_selector": "a"},
    ],
)
def test_proposals_reject_other_strategies_and_definition_settings(data):
    with pytest.raises(ValidationError):
        RecipeProposal.model_validate(data, strict=True)
