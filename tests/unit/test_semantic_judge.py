"""Semantic judge contract and model choice resolution (QUAL-005, QUAL-006, AI-001, P-001)."""

import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from rastro import ConfigurationError, InferencePolicy, ModelError, ModelRequest
from rastro.application.model_choice import resolve_model
from rastro.application.semantic_judge import (
    MAX_VALUE_CHARS,
    FieldVerdict,
    apply_verdicts,
    judge,
    judge_request,
    parse_verdicts,
)
from rastro.domain.consistency import ConsistencyReport
from rastro.domain.intelligence import proposal_schema
from rastro.domain.recipe_compare import CandidateEvaluation

POLICY = InferencePolicy()


class Offer(BaseModel):
    title: str = Field(description="Job title")
    city: str


def _evaluation(decision="promote", historical=1, completeness=1.0):
    return CandidateEvaluation(
        decision=decision,
        reasons=("all_gates_passed",) if decision == "promote" else ("x",),
        current_status="success",
        current_records=2,
        historical_fixtures=historical,
        historical_passed=historical,
        indicators=ConsistencyReport(
            records=2,
            completeness=completeness,
            structural_validity=1.0,
            semantic_confidence=None,
            verdict="not_assessed",
            results=(),
        ),
        regressions=(),
    )


def test_request_labels_values_untrusted_clips_them_and_sets_its_schema():
    long = "x" * (MAX_VALUE_CHARS + 50)
    rows = [{"title": long}, {"title": long}, {"title": "B"}]
    request = judge_request(Offer, ["title"], rows, [{"title": "A"}])
    (field,) = json.loads(request.content)["fields"]
    assert field["description"] == "Job title" and field["reference_values"] == ["A"]
    clipped, other = field["untrusted_candidate_values"]
    assert len(clipped) == MAX_VALUE_CHARS and other == "B"
    assert request.output_schema["required"] == ["title"]
    assert "untrusted" in request.system
    assert ModelRequest(system="s", content="c").output_schema == proposal_schema()


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        '{"title": {"verdict": "match", "confidence": 0.5}, "extra": {}}',
        '{"title": {"verdict": "maybe", "confidence": 0.5}}',
        '{"title": {"verdict": "match", "confidence": 2}}',
        '{"title": {"verdict": "match", "confidence": 0.5}, "title": {}}',
        "[]",
    ],
)
def test_out_of_contract_answers_are_invalid(output):
    with pytest.raises(ModelError) as caught:
        parse_verdicts(output, ["title"], POLICY)
    assert caught.value.code == "invalid_model_response"


@pytest.mark.usefixtures("offline_cli_loop")
def test_oversized_answers_and_failures_yield_no_verdict():
    tiny = InferencePolicy(max_response_bytes=10)
    with pytest.raises(ModelError, match="model_response_limit"):
        parse_verdicts('{"title": {"verdict": "match", "confidence": 1}}', ["title"], tiny)

    class Down:
        async def generate(self, request, policy):
            raise ModelError("model_unavailable")

    request = judge_request(Offer, ["title"], [], [])
    assert (
        asyncio.run(
            judge(Down(), POLICY, request, ["title"]), loop_factory=asyncio.SelectorEventLoop
        )
        is None
    )


def test_mismatch_blocks_unsure_needs_strong_gates_failure_blocks():
    match = {"title": FieldVerdict(verdict="match", confidence=0.8)}
    judged = apply_verdicts(_evaluation(), match)
    assert judged.decision == "promote" and judged.indicators.semantic_confidence == 0.8
    assert judged.indicators.verdict == "consistent"
    mismatch = {"title": FieldVerdict(verdict="mismatch", confidence=0.9)}
    blocked = apply_verdicts(_evaluation(), mismatch)
    assert blocked.decision == "quarantine" and blocked.reasons == ("semantic_mismatch",)
    assert blocked.indicators.semantic_confidence == 0 and blocked.indicators.verdict == (
        "inconsistent"
    )
    unsure = {"title": FieldVerdict(verdict="unsure", confidence=0.9)}
    assert apply_verdicts(_evaluation(), unsure).decision == "promote"
    weak = apply_verdicts(_evaluation(completeness=0.9), unsure)
    assert weak.reasons == ("semantic_unsure",)
    assert apply_verdicts(_evaluation(historical=0), unsure).decision == "quarantine"
    assert apply_verdicts(_evaluation(), None).reasons == ("semantic_judge_failed",)
    assert apply_verdicts(_evaluation("reject"), mismatch).decision == "reject"


def test_resolve_model_accepts_any_port_and_rejects_other_objects(monkeypatch):
    class Port:
        async def generate(self, request, policy):
            return "{}"

    port = Port()
    assert resolve_model(None) is None and resolve_model(port) is port
    with pytest.raises(ConfigurationError):
        resolve_model(42)
    import rastro

    seen = []
    monkeypatch.setattr(
        rastro, "model", lambda spec=None, **o: seen.append((spec, o)) or port, raising=False
    )
    assert resolve_model("env") is port and resolve_model("x", allow_remote=True) is port
    assert seen == [(None, {}), ("x", {"allow_remote": True})]
