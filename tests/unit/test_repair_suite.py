"""The repair suite measures; it never asserts that repair succeeds (REPAIR-001-005, DIFF-002)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SUITE = Path(__file__).parents[2] / "benchmarks" / "repair_suite"


def _load_measure():
    spec = importlib.util.spec_from_file_location("repair_suite_measure", SUITE / "measure.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_cases_produce_the_result_shape(tmp_path: Path) -> None:
    measure = _load_measure()
    output = tmp_path / "results.json"
    code = measure.main_cli(
        [
            "--case",
            "sodre:field_class_rename",
            "--case",
            "tickantel:page_emptied",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [(c["page"], c["mutation"]) for c in data["cases"]] == [
        ("sodre", "field_class_rename"),
        ("tickantel", "page_emptied"),
    ]
    for case in data["cases"]:
        assert case["outcome"] in measure.OUTCOMES
        assert case["kind"] in {"structural", "trap"}
        assert case["model_calls"] == 0
        assert case["seconds"] >= 0
    assert sum(data["counts"].values()) == 2


def test_every_case_has_expected_rows_and_a_real_change() -> None:
    measure = _load_measure()
    for page, cases in measure.CASES.items():
        html = (SUITE / "cases" / page / "page.html").read_text(encoding="utf-8")
        assert json.loads((SUITE / "cases" / page / "expected.json").read_text(encoding="utf-8"))
        assert all(case.transform(html) != html for case in cases)
