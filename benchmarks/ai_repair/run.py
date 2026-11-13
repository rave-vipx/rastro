"""Run the AI repair benchmark (QUAL-001, REPAIR-001-005, AI-001).

Offline executors (default, no network, no model):
    python -m benchmarks.ai_repair.run
Any ModelPort, local only and never in CI (see docs/ai-evidence.md):
    python -m benchmarks.ai_repair.run --executor ollama:qwen3:4b --output live.json
    python -m benchmarks.ai_repair.run --executor spec:"<RASTRO_MODEL spec>" --output x.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rastro
from benchmarks.ai_repair.baseline import run_baseline
from benchmarks.ai_repair.cases import CASES, Case
from benchmarks.ai_repair.executors import Verdict, run_rastro
from benchmarks.ai_repair.oracle import ScriptedOracle
from benchmarks.ai_repair.scoring import summarize
from rastro.ports.model import ModelPort

HERE = Path(__file__).parent
OFFLINE = (
    "deterministic",
    "baseline",
    "oracle_correct",
    "oracle_wrong_recipe",
    "oracle_adversarial",
)
PLACEHOLDER = {"provider": "ollama", "model": "injected"}  # replaced by the injected ModelPort
Executor = Callable[[Case], Verdict]


def _model(
    name: str, options: dict[str, Any]
) -> tuple[dict[str, Any], Callable[[], ModelPort | None]]:
    """The job's ``[model]`` table and a factory for the ModelPort injected behind it."""
    if name.startswith("oracle_"):
        mode: Any = name.removeprefix("oracle_")
        return PLACEHOLDER, lambda: ScriptedOracle(CASES, mode)
    if name.startswith("ollama:"):
        return {"provider": "ollama", "model": name.removeprefix("ollama:")}, lambda: None
    if name.startswith("spec:"):
        port = rastro.model(name.removeprefix("spec:"), **options)
        return PLACEHOLDER, lambda: port
    raise SystemExit(f"unknown executor {name!r}")


def executor(name: str, options: dict[str, Any] | None = None) -> Executor:
    """``deterministic``, ``baseline``, ``oracle_<mode>``, ``ollama:<model>`` or ``spec:<spec>``.

    ``options`` are ``rastro.model`` keywords for ``spec:`` executors (``json_path``, ...).
    """
    if name == "deterministic":
        return run_rastro
    if name == "baseline":
        return run_baseline
    provider, fake = _model(name, options or {})
    return lambda case: run_rastro(case, provider=provider, fake=fake())


def run(
    names: list[str], cases: tuple[Case, ...] = CASES, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in names:
        verdicts = [executor(name, options)(case) for case in cases]
        results[name] = {
            "summary": summarize(verdicts),
            "cases": [v.as_dict() for v in verdicts],
        }
    return results


def environment() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "git_head": head or None,
        "rastro": rastro.__version__,
        "python": sys.version.split()[0],
        "cases": len(CASES),
    }


def table(results: dict[str, Any]) -> str:
    rows = [
        "| Executor | Correct | Wrong (silent) | Refused | Model calls |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, item in results.items():
        s = item["summary"]
        cells = [
            f"{s[v]['count']}/{s['cases']} ({s[v]['wilson95'][0]:.2f}-{s[v]['wilson95'][1]:.2f})"
            for v in ("correct", "wrong", "refused")
        ]
        rows.append(f"| {name} | {' | '.join(cells)} | {s['model_calls']} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", action="append", help="default: every offline executor")
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    parser.add_argument(
        "--model-options", type=json.loads, default={}, help="JSON rastro.model keywords"
    )
    args = parser.parse_args(argv)
    results = run(args.executor or list(OFFLINE), options=args.model_options)
    report = {"environment": environment(), "results": results}
    args.output.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
