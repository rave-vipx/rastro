"""Verify the release evidence oracle and scoring (DIFF-001, QUAL-003)."""

import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from pytest_socket import disable_socket, enable_socket

from benchmarks import baseline, heldout, rastro_runner
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


# Held-out DIFF-001 evaluation (benchmarks/heldout.py, benchmarks/heldout.json).

HELDOUT = {source.name: source for source in heldout.load_sources()}
SILENT = {"related_product_added", "sponsored_duplicate", "price_class_reassigned"}


def offline_loop() -> asyncio.AbstractEventLoop:
    # The loop's private wake-up socket pair is not network access (see conftest).
    enable_socket()
    try:
        return asyncio.new_event_loop()
    finally:
        disable_socket()


def test_heldout_corpus_is_disjoint_and_every_mutation_changes_the_page() -> None:
    regression = {case.document.html for case in CASES.values()}
    assert not regression & {source.html for source in HELDOUT.values()}
    names = [(source.name, m.name) for source in HELDOUT.values() for m in source.mutations]
    assert len(names) == len(set(names)) == 24
    for source in HELDOUT.values():
        assert (source.rastro_recipe is None) is (source.kind in heldout.STRUCTURED)
        for mutation in source.mutations:
            assert mutation.apply(source.html) != source.html
            if mutation.requires_warning is False:
                # Non-warning oracles keep the original records: data is still on the page.
                assert mutation.expected == source.expected


def test_heldout_mutation_must_apply() -> None:
    mutation = heldout.Mutation(name="x", kind="k", replace=(("absent", "y"),), expected=())
    with pytest.raises(ValueError, match="does not apply"):
        mutation.apply("<p>page</p>")


def test_heldout_classification_separates_silent_errors() -> None:
    expected = HELDOUT["hardware_cards"].expected
    extra = (*expected, expected[0])
    silent = heldout.classify(heldout.Run("success", extra), expected)
    assert silent["outcome"] == "silent_error" and silent["extra_records"] == 1
    warned = heldout.classify(heldout.Run("success", expected), expected, requires_warning=True)
    assert warned["outcome"] == "silent_error"
    explicit = heldout.classify(heldout.Run("partial_success", expected[:2]), expected)
    assert explicit["outcome"] == "explicit_non_success" and explicit["correct_records"] == 2
    scaled = expected[0].model_copy(update={"price": Decimal("24.900")})
    exact = heldout.classify(heldout.Run("success", (scaled, *expected[1:])), expected)
    assert exact["outcome"] == "correct_success"


def test_heldout_effort_counters() -> None:
    mapping = HELDOUT["hardware_cards"].baseline_mappings[0]
    assert heldout.config_size([mapping]) == {"entries": 5, "tokens": 35, "characters": 103}

    def sample() -> int:
        """Docstring lines are not configuration."""
        # Neither are comments.
        return 1

    assert heldout.logical_lines(sample) == 2


def test_baseline_reports_malformed_jsonld_instead_of_raising() -> None:
    source = HELDOUT["books_jsonld_blocks"]
    html = next(m for m in source.mutations if m.name == "one_block_malformed").apply(source.html)
    status, records = baseline.run(html, source.baseline_mappings, heldout.Product)
    assert status == "partial_success" and len(records) == 2


def test_baseline_structured_adapter_handles_offer_lists_and_microdata() -> None:
    fields = tuple(heldout.Product.model_fields)
    source = HELDOUT["outdoor_jsonld"]
    html = next(m for m in source.mutations if m.name == "offers_as_list").apply(source.html)
    (mapping,) = baseline.structured_mappings(html, fields)
    assert mapping.fields["price"] == "/offers/0/price" and mapping.item_type == "Product"
    (microdata,) = baseline.structured_mappings(HELDOUT["pantry_microdata"].html, fields)
    assert microdata.strategy == "css"
    assert baseline.structured_mappings(HELDOUT["deli_table"].html, fields) == ()


def test_heldout_report_counts_and_known_differences() -> None:
    report = heldout.run(repetitions=1, warmup=0, model_runs=2, loop_factory=offline_loop)
    effort = report["integration_effort"]
    assert effort["totals"]["baseline"]["entries"] == 25
    assert effort["totals"]["rastro"]["entries"] == effort["totals"]["baseline_adapted"]["entries"]
    for row in effort["per_source"]:
        assert row["rastro_discovered"] is (row["kind"] in heldout.STRUCTURED)
        assert row["rastro_discovery_with_currency_field_name"] == "insufficient_evidence"
    mutations = report["mutations"]
    for name, summary in mutations["summary"].items():
        assert (
            (summary["correct_success"] + summary["explicit_non_success"] + summary["silent_error"])
            == summary["mutations"]
            == 24
        )
        silent = {
            row["mutation"]
            for row in mutations["mutations"]
            if row[name]["outcome"] == "silent_error"
        }
        assert silent == SILENT
    outcome = {row["mutation"]: row for row in mutations["mutations"]}
    assert outcome["offers_as_list"]["baseline_adapted"]["outcome"] == "correct_success"
    assert outcome["offers_as_list"]["rastro_memory"]["outcome"] == "explicit_non_success"
    assert outcome["brand_name_added"]["rastro_memory"]["outcome"] == "correct_success"
    assert outcome["brand_name_added"]["rastro_rediscover"]["outcome"] == "explicit_non_success"
    model = report["model_reuse"]["summary"]
    css_sources = sum(source.rastro_recipe is not None for source in HELDOUT.values())
    assert model["scripted_valid_first/memory"]["inference_calls"] == css_sources
    assert model["scripted_valid_first/no_memory"]["inference_calls"] == 2 * css_sources
    assert model["scripted_one_rejection/memory"]["inference_calls"] == 2 * css_sources
    checklist = {row["item"]: row for row in report["visibility"]["checklist"]}
    assert checklist["explicit_outcome_status"] == {
        "item": "explicit_outcome_status",
        "baseline": True,
        "rastro": True,
    }
    missing = "issue_names_field_for_missing_required_value"
    assert all(row["rastro"] for item, row in checklist.items() if item != missing)
    # Recorded Rastro limitation: the missing required field is known but not reported.
    assert checklist[missing]["rastro"] is False
    assert report["visibility"]["missing_value_issues"] == [
        {"code": "missing_evidence", "record_index": 2, "field": None}
    ]
    assert len(report["latency"]) == len(HELDOUT)


@pytest.mark.parametrize("budget", [(0, 0, 1), (1, -1, 1), (1, 0, 0)])
def test_invalid_heldout_budget(budget) -> None:
    with pytest.raises(ValueError):
        heldout.run(*budget)
