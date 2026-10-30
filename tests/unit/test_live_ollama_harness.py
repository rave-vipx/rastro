"""Offline checks for the opt-in live Ollama harness inputs (AI-001, INT-002, REC-004)."""

import pytest

from benchmarks.live_ollama import EXPECTED, INPUTS, SOURCE, Product, extract
from rastro import Outcome, Rastro, Recipe


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(INPUTS))
async def test_inputs_have_no_deterministic_route(tmp_path, name) -> None:
    report = await extract(None, tmp_path / "memory.db", html=INPUTS[name])
    assert report["status"] == "insufficient_evidence"
    assert report["issues"] == ["no_valid_recipe"]
    assert report["recipe_attempts"] == [] and report["inference_calls"] == 0
    assert report["stored_recipe"] is None


@pytest.mark.parametrize("name", sorted(INPUTS))
def test_expected_record_is_reachable_by_a_declarative_recipe(name) -> None:
    # A model failure on either page is a proposal-quality result, not an impossible input.
    recipe = Recipe(
        strategy="css",
        record_selector="article.product-card",
        fields={"name": "h2.title::text", "price": "span.amount::text"},
        require_evidence=True,
    )
    result = Rastro().extract_html(INPUTS[name], source=SOURCE, schema=Product, recipe=recipe)
    assert result.status == Outcome.SUCCESS
    assert [record.data.model_dump(mode="json") for record in result.records] == [EXPECTED]
