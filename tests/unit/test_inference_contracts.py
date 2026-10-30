import json

import pytest
from pydantic import ValidationError

from rastro import InferencePolicy, ModelRequest, RecipeProposal
from rastro.domain.intelligence import proposal_schema


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
