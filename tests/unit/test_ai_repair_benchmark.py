"""AI repair benchmark scoring and offline execution (QUAL-001, REPAIR-001-005, AI-001).

The scripted oracle is a fake ModelPort: these tests check the harness and Rastro's
gates, never the quality of a real model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from benchmarks.ai_repair import export
from benchmarks.ai_repair.baseline import run_baseline
from benchmarks.ai_repair.cases import CASES
from benchmarks.ai_repair.executors import Verdict, judge, run_rastro
from benchmarks.ai_repair.layouts import RECORDS, expected, render
from benchmarks.ai_repair.oracle import ScriptedOracle
from benchmarks.ai_repair.run import PLACEHOLDER, executor, table
from benchmarks.ai_repair.scoring import summarize, wilson
from rastro.application.proposals import build_proposal_request
from rastro.domain.intelligence import InferencePolicy, ModelError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
CASE = {case.name: case for case in CASES}


def _verdict(verdict: str, kind: str = "structural", calls: int = 0) -> Verdict:
    return Verdict("x", kind, verdict, "d", calls, 0.0)


def test_wilson_interval_matches_reference_values() -> None:
    assert wilson(0, 28) == pytest.approx((0.0, 0.1206), abs=1e-4)
    assert wilson(14, 28) == pytest.approx((0.3263, 0.6737), abs=1e-4)
    assert wilson(28, 28)[1] == pytest.approx(1.0)
    assert wilson(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError, match="between"):
        wilson(3, 2)


def test_summary_counts_each_verdict_kind_and_model_calls() -> None:
    summary = summarize(
        [_verdict("correct", calls=1), _verdict("wrong", "trap"), _verdict("refused", calls=2)]
    )
    assert summary["cases"] == 3
    assert summary["model_calls"] == 3
    assert [summary[v]["count"] for v in ("correct", "wrong", "refused")] == [1, 1, 1]
    assert summary["by_kind"]["trap"] == {"correct": 0, "wrong": 1, "refused": 0}
    assert "| x | 1/3 (0.06-0.79) | 1/3" in table({"x": {"summary": summary}})


def test_judge_is_exact_order_insensitive_and_ignores_absent_optionals() -> None:
    case = CASE["table-optional_thinned"]
    rows = expected(case.after)
    assert judge(list(reversed(rows)), case) == "correct"
    assert judge([{k: v for k, v in r.items() if v is not None} for r in rows], case) == "correct"
    assert judge(rows[:-1], case) == "wrong"
    assert judge([*rows, dict(RECORDS[0], id="promo-1")], case) == "wrong"
    swapped = [dict(r, title=r["performer"], performer=r["title"]) for r in rows]
    assert judge(swapped, case) == "wrong"


def test_suite_has_20_to_30_cases_with_traps_and_committed_files_in_sync() -> None:
    assert 20 <= len(CASES) <= 30
    assert {c.kind for c in CASES} == {"structural", "trap"}
    for relative, content in export.files().items():
        assert (export.ROOT / relative).read_text(encoding="utf-8") == content, relative


def test_oracle_answers_from_the_prompt_alone() -> None:
    case = CASE["cards-container_restructured"]
    policy = InferencePolicy()

    def ask(oracle: ScriptedOracle, html: str) -> str:
        request = build_proposal_request(
            fields=("id", "title"), required={"id"}, html=html, failure="recipe_repair"
        )
        return asyncio.run(oracle.generate(request, policy), loop_factory=asyncio.SelectorEventLoop)

    answer = json.loads(ask(ScriptedOracle(CASES), render(case.after)))
    assert answer["record_selector"] == case.correct_recipe()["record_selector"]
    bad = json.loads(ask(ScriptedOracle(CASES, "adversarial"), render(case.after)))
    assert bad["fields"]["title"] == "p.card-artist::text"
    with pytest.raises(ModelError):
        ask(ScriptedOracle(CASES), "<p>unknown page</p>")


def test_deterministic_repair_runs_offline_and_promotes_a_class_rename() -> None:
    verdict = run_rastro(CASE["cards-class_rename"])
    assert (verdict.verdict, verdict.detail, verdict.model_calls) == ("correct", "promoted", 0)


def test_a_wrong_meaning_proposal_is_refused_not_served() -> None:
    oracle = ScriptedOracle(CASES, "adversarial")
    verdict = run_rastro(CASE["cards-semantic_swap"], provider=PLACEHOLDER, fake=oracle)
    assert verdict.verdict == "refused"
    assert oracle.calls == 1


def test_escalation_consults_the_model_only_after_deterministic_repair_fails() -> None:
    run = executor("escalate+oracle_compatible")
    assert run(CASE["cards-class_rename"]).model_calls == 0
    restructured = run(CASE["cards-container_restructured"])
    assert (restructured.verdict, restructured.detail) == ("correct", "escalated_promoted")
    assert restructured.model_calls == 1


def test_hand_written_baseline_serves_decoys_silently() -> None:
    assert run_baseline(CASE["cards-decoy_blocks"]).verdict == "wrong"
    assert run_baseline(CASE["cards-semantic_swap"]).verdict == "refused"
