"""Run DIFF-001's offline extraction comparison: python -m benchmarks.run --output report.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns

from pydantic import TypeAdapter

from benchmarks import baseline, rastro_runner
from benchmarks.contracts import Case, Observation
from benchmarks.scoring import Score, percentile95, ratio, score

ROOT = Path(__file__).resolve().parent


def _git(*arguments: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *arguments], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


class CaseReport(Score):
    case: str
    category: str
    status: str
    inference_calls: int
    latency_ms: dict[str, float | list[float]]


def load_cases() -> list[Case]:
    return [
        case.expanded()
        for case in TypeAdapter(list[Case]).validate_json((ROOT / "corpus.json").read_bytes())
    ]


def run(repetitions: int = 50, warmup: int = 5) -> dict[str, object]:
    if not 1 <= repetitions <= 1000 or not 0 <= warmup <= 100:
        raise ValueError("Use 1-1000 repetitions and 0-100 warmups")
    cases = load_cases()
    runners = {"scrapling_pydantic": baseline.extract, "rastro": rastro_runner.extract}
    reports: dict[str, list[CaseReport]] = {name: [] for name in runners}
    logger = logging.getLogger("rastro")
    disabled = logger.disabled
    logger.disabled = True
    try:
        for case in cases:
            samples: dict[str, list[float]] = {name: [] for name in runners}
            observations: dict[str, Observation] = {}
            for iteration in range(warmup + repetitions):
                order = list(runners) if iteration % 2 == 0 else list(reversed(runners))
                for name in order:
                    start = perf_counter_ns()
                    observation = runners[name](case.document)
                    elapsed_ms = (perf_counter_ns() - start) / 1_000_000
                    if iteration >= warmup:
                        samples[name].append(elapsed_ms)
                        previous = observations.setdefault(name, observation)
                        if previous != observation:
                            raise ValueError("Non-deterministic benchmark observation")
            for name in runners:
                observation = observations[name]
                reports[name].append(
                    {
                        "case": case.name,
                        "category": case.category,
                        "status": observation.status,
                        **score(case, observation),
                        "inference_calls": observation.inference_calls,
                        "latency_ms": {
                            "median": statistics.median(samples[name]),
                            "p95": percentile95(samples[name]),
                            "samples": samples[name],
                        },
                    }
                )
    finally:
        logger.disabled = disabled
    # Keep summaries derived from primitive counts so independent tools can recompute them.
    summaries = {}
    for name, rows in reports.items():
        counts = {
            "field_tp": sum(row["field_tp"] for row in rows),
            "field_fp": sum(row["field_fp"] for row in rows),
            "field_fn": sum(row["field_fn"] for row in rows),
            "correct_records": sum(row["correct_records"] for row in rows),
            "expected_records": sum(row["expected_records"] for row in rows),
        }
        announced = sum(row["announced_success"] is True for row in rows)
        warning_cases = sum(row["requires_warning"] is True for row in rows)
        precisions = [row["field_precision"] for row in rows if row["field_precision"] is not None]
        recalls = [row["field_recall"] for row in rows if row["field_recall"] is not None]
        summaries[name] = {
            **counts,
            "cases": len(rows),
            "announced_successes": announced,
            "false_successes": sum(row["false_success"] is True for row in rows),
            "warning_cases": warning_cases,
            "warnings_detected": sum(row["warning_detected"] is True for row in rows),
            "exact_record_sets": sum(row["exact_records"] is True for row in rows),
            "micro_precision": ratio(counts["field_tp"], counts["field_tp"] + counts["field_fp"]),
            "micro_recall": ratio(counts["field_tp"], counts["field_tp"] + counts["field_fn"]),
            "macro_precision": statistics.mean(precisions) if precisions else None,
            "macro_recall": statistics.mean(recalls) if recalls else None,
            "complete_record_recall": ratio(counts["correct_records"], counts["expected_records"]),
            "false_success_rate": ratio(
                sum(row["false_success"] is True for row in rows), announced
            ),
            "warning_detection_rate": ratio(
                sum(row["warning_detected"] is True for row in rows), warning_cases
            ),
        }
    return {
        "format_version": 1,
        "scope": "offline supplied-HTML extraction; known mappings/fallbacks",
        "created_at": datetime.now(UTC).isoformat(),
        "repetitions": repetitions,
        "warmup": warmup,
        "environment": {
            "python": sys.version.split()[0],
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "packages": {
                name: version(name) for name in ("rastro-engine", "scrapling", "pydantic", "lxml")
            },
        },
        "git_revision": _git("rev-parse", "HEAD"),
        "git_dirty": None if (status := _git("status", "--porcelain")) is None else bool(status),
        "source_hashes": {
            path.relative_to(ROOT.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [
                    *ROOT.glob("*.py"),
                    ROOT / "corpus.json",
                    ROOT.parent / "uv.lock",
                    *(ROOT.parent / "src" / "rastro").rglob("*.py"),
                ]
            )
        },
        "unmeasured": [
            "network",
            "JavaScript rendering",
            "pagination",
            "rate limiting",
            "live inference",
            "repair",
            "checkpoint recovery",
            "native peak memory",
            "human effort",
        ],
        "summaries": summaries,
        "cases": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    report = run(args.repetitions, args.warmup)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summaries"], indent=2))


if __name__ == "__main__":
    main()
