"""Offline checks for the opt-in live Ollama harness inputs (AI-001, INT-002, REC-004)."""

import pytest

from benchmarks.live_ollama import (
    EXPECTED,
    INPUTS,
    SOURCE,
    Product,
    collect,
    extract,
    merge,
    summarize_model,
)
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


def _phase(status: str, *, reused: bool = False, calls: int = 1, stored: bool = True) -> dict:
    return {
        "status": status,
        "recipe_reused": reused,
        "inference_calls": calls,
        "model_port_calls": calls,
        "inference_seconds": 1.5 * calls,
        "total_seconds": 0.01,
        "records": [{"data": EXPECTED}] if status == "success" else [],
        "stored_recipe": {"strategy": "css"} if stored else None,
    }


def test_model_summary_is_recomputed_from_raw_phases() -> None:
    report = {
        "trials": [
            {"learn": _phase("success"), "reuse": _phase("success", reused=True, calls=0)},
            {
                "learn": _phase("insufficient_evidence", calls=2, stored=False),
                "reuse": _phase("insufficient_evidence", calls=0, stored=False),
            },
        ],
        "repetitions": {
            "product_card": {"runs": 1, "success": 1, "results": [_phase("success")]},
        },
        "failures": [_phase("transient_failure", stored=False), _phase("success")],
    }
    summary = summarize_model(report)
    assert summary["trials"] == 2
    assert (summary["learn_success"], summary["activated"], summary["reuse_success"]) == (1, 1, 1)
    assert summary["learn_failures"] == {"insufficient_evidence": 1}
    assert summary["learn_matches_expected"] == 1
    assert summary["inference_calls"] == 4
    assert summary["learn_inference_seconds"] == {"min": 1.5, "median": 2.25, "max": 3.0}
    assert summary["repetition_success"] == {"product_card": "1/1"}
    assert (summary["failure_scenarios_explicit"], summary["failure_scenarios"]) == (1, 2)


def test_report_versioning_keeps_every_previous_run() -> None:
    first = merge(None, {"id": 1})
    assert first == {"format_version": 2, "runs": [{"id": 1}], "previous_runs": []}
    legacy = merge({"scope": "v1", "learn": {}}, {"id": 2})
    assert legacy["previous_runs"] == [{"format_version": 1, "scope": "v1", "learn": {}}]
    chained = merge(legacy, {"id": 3})
    assert chained["runs"] == [{"id": 3}]
    assert chained["previous_runs"] == [legacy["previous_runs"][0], {"id": 2}]


@pytest.mark.asyncio
@pytest.mark.parametrize("trials,repetitions", [(0, 1), (21, 1), (1, -1), (1, 21)])
async def test_collect_rejects_invalid_budgets_before_any_request(trials, repetitions) -> None:
    with pytest.raises(ValueError):
        await collect("unused", repetitions, trials)
