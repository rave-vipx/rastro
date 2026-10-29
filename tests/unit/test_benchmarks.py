"""Verify the release evidence oracle and scoring (DIFF-001, QUAL-003)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from benchmarks import baseline, rastro_runner
from benchmarks.contracts import Observation
from benchmarks.run import load_cases, run
from benchmarks.scoring import percentile95, score

CASES = {case.name: case for case in load_cases()}


def test_field_scoring_counts_wrong_values_and_duplicate_records() -> None:
    case = CASES["metadata"]
    original = case.expected[0]
    wrong = original.model_copy(update={"price": Decimal("99")})
    scored = score(case, Observation("success", (original, wrong)))
    assert (scored["field_tp"], scored["field_fp"], scored["field_fn"]) == (4, 4, 0)
    assert scored["correct_records"] == 1
    assert scored["false_success"] is True
    duplicate = score(case, Observation("success", (original, original)))
    assert duplicate["field_fp"] == 4
    assert duplicate["exact_records"] is False


def test_scoring_ignores_order_and_decimal_representation() -> None:
    case = CASES["static"]
    equivalent = case.expected[0].model_copy(update={"price": Decimal("12.500")})
    result = score(case, Observation("success", (case.expected[1], equivalent)))
    assert result["exact_records"] is True
    assert result["field_fp"] == result["field_fn"] == 0


def test_empty_output_and_required_warning_are_not_false_success() -> None:
    case = CASES["missing"]
    rejected = score(case, Observation("missing_data", ()))
    assert rejected["field_precision"] is rejected["field_recall"] is None
    assert rejected["warning_detected"] is True
    assert score(case, Observation("success", ()))["false_success"] is True
    partial = CASES["partial"]
    assert score(partial, Observation("success", partial.expected))["false_success"] is True


@pytest.mark.parametrize("name", list(CASES))
@pytest.mark.parametrize("extract", [baseline.extract, rastro_runner.extract])
def test_corpus_outcomes(name, extract) -> None:
    case = CASES[name]
    observation = extract(case.document)
    result = score(case, observation)
    assert result["false_success"] is (name == "semantic_trap")
    assert result["exact_records"] is (name not in {"broken", "js_shell", "semantic_trap"})
    assert result["warning_detected"] is (case.requires_warning and name != "semantic_trap")
    assert observation.inference_calls == 0


def test_report_recomputes_counts_and_records_samples() -> None:
    report = run(repetitions=2, warmup=1)
    for name, rows in report["cases"].items():
        summary = report["summaries"][name]
        assert summary["false_successes"] == 1
        assert summary["false_success_rate"] == 1 / 8
        assert summary["warning_detection_rate"] == 6 / 7
        assert summary["field_tp"] == sum(row["field_tp"] for row in rows)
        for row in rows:
            latency = row["latency_ms"]
            assert len(latency["samples"]) == 2
            assert 0 <= latency["median"] <= latency["p95"]
    assert len(report["source_hashes"]["benchmarks/corpus.json"]) == 64


@pytest.mark.parametrize("repetitions,warmup", [(0, 0), (1001, 0), (1, -1), (1, 101)])
def test_invalid_benchmark_budget(repetitions, warmup) -> None:
    with pytest.raises(ValueError):
        run(repetitions=repetitions, warmup=warmup)


def test_percentile_uses_nearest_rank() -> None:
    assert percentile95(list(range(1, 101))) == 95
    assert percentile95([2.0]) == 2


def test_harness_detects_nondeterminism_and_restores_logger(monkeypatch) -> None:
    import logging

    logger = logging.getLogger("rastro")
    previous = logger.disabled
    calls = 0
    real = baseline.extract

    def inconsistent(document):
        nonlocal calls
        calls += 1
        return replace(real(document), status=str(calls))

    monkeypatch.setattr(baseline, "extract", inconsistent)
    with pytest.raises(ValueError, match="Non-deterministic"):
        run(repetitions=2, warmup=0)
    assert logger.disabled is previous


def test_report_runs_without_git_in_source_distribution(monkeypatch) -> None:
    import subprocess

    def no_repository(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "check_output", no_repository)
    report = run(repetitions=1, warmup=0)
    assert report["git_revision"] is None
    assert report["git_dirty"] is None
