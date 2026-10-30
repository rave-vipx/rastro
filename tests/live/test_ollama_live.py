"""Opt-in live Ollama evidence; skipped unless RASTRO_LIVE_OLLAMA_MODEL names an installed model.

Run: RASTRO_LIVE_OLLAMA_MODEL=qwen3:4b uv run --extra ollama pytest tests/live --no-cov
"""

import asyncio
import os

import pytest

from benchmarks.live_ollama import collect

MODEL = os.environ.get("RASTRO_LIVE_OLLAMA_MODEL")
pytestmark = [
    pytest.mark.skipif(not MODEL, reason="Set RASTRO_LIVE_OLLAMA_MODEL to run live Ollama"),
    pytest.mark.enable_socket,
]
FAILURES = {
    "endpoint_unavailable": ("transient_failure", ["model_unavailable"]),
    "model_not_installed": ("transient_failure", ["model_unavailable"]),
    "output_token_budget": ("insufficient_evidence", ["invalid_model_response"]),
    "response_byte_budget": ("insufficient_evidence", ["model_response_limit"]),
    "phase_timeout": ("transient_failure", ["model_timeout"]),
    "malformed_provider_body": ("insufficient_evidence", ["invalid_model_response"]),
    "unparseable_model_content": ("insufficient_evidence", ["invalid_model_candidate"]),
}


def test_live_learning_activation_reuse_and_failures() -> None:
    assert MODEL is not None
    report = asyncio.run(collect(MODEL, repetitions=1))
    assert all(item["recipe_attempts"] == [] for item in report["no_model_precheck"].values())
    learn, reuse = report["learn"], report["reuse"]
    assert learn["status"] == "success" and learn["recipe_reused"] is False
    assert learn["inference_calls"] >= 1
    assert learn["stored_recipe"] == learn["learned_recipe"]
    assert reuse["status"] == "success" and reuse["recipe_reused"] is True
    assert reuse["inference_calls"] == 0 and reuse["model_port_calls"] == 0
    assert [record["data"] for record in reuse["records"]] == [report["expected_record"]]
    for item in report["failures"]:
        assert item["status"] != "success" and item["records"] == []
        assert item["stored_recipe"] is None
        if item["scenario"] in FAILURES:
            assert (item["status"], item["issues"]) == FAILURES[item["scenario"]]
