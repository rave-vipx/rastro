"""Attribute extraction latency to functions: python -m benchmarks.profile_latency.

Profiles the same two executors as `benchmarks.run` on three representative corpus
cases and records the top cumulative functions per call. Measurement only: no engine
change is implied by a hotspot (spec P-010).
"""

from __future__ import annotations

import argparse
import cProfile
import json
import logging
import platform
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from subprocess import DEVNULL, CalledProcessError, check_output
from time import perf_counter
from typing import TypedDict

from benchmarks import baseline, rastro_runner
from benchmarks.contracts import Case, Document, Observation
from benchmarks.run import load_cases
from rastro import Evidence, Recipe, Source, schema_fingerprint
from rastro.application.extraction import validate_recipe

ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ("static", "metadata", "large")
EXECUTORS: dict[str, Callable[[Document], Observation]] = {
    "scrapling_pydantic": baseline.extract,
    "rastro": rastro_runner.extract,
}


class Hotspot(TypedDict):
    function: str
    cumulative_ms_per_call: float
    self_ms_per_call: float
    share: float
    calls_per_call: float


class ProfileRow(TypedDict):
    case: str
    category: str
    executor: str
    status: str
    records: int
    calls: int
    warmup: int
    per_call_ms: float
    unprofiled_ms_per_call: float
    top: list[Hotspot]


class Report(TypedDict):
    format_version: int
    scope: str
    created_at: str
    calls: int
    warmup: int
    environment: dict[str, object]
    git_revision: str | None
    unmeasured: list[str]
    fixed_costs: dict[str, float]
    cases: list[ProfileRow]


def _git(*arguments: str) -> str | None:
    try:
        return check_output(["git", *arguments], cwd=ROOT, text=True, stderr=DEVNULL).strip()
    except (FileNotFoundError, CalledProcessError):
        return None


def _module(filename: str) -> str:
    """Dotted module name for a profile label, independent of the checkout path."""
    if filename.startswith("<"):
        return filename.strip("<>").replace(" ", "_")
    path = Path(filename)
    parts = path.with_suffix("").parts
    for marker in ("site-packages", "dist-packages"):
        if marker in parts:
            return ".".join(parts[parts.index(marker) + 1 :])
    repo = ROOT.parent
    try:
        relative = path.resolve().relative_to(repo)
    except ValueError:
        pass
    else:
        dotted = relative.with_suffix("").parts
        return ".".join(dotted[1:] if dotted[:1] == ("src",) else dotted)
    return path.stem


def _function(filename: str, name: str) -> str:
    """`module:function`, with builtins and C entry points collapsed to one namespace."""
    if filename == "~":
        return f"builtins:{name}"
    return f"{_module(filename)}:{name}"


def profile(
    executor: str,
    case: Case,
    *,
    calls: int,
    warmup: int,
    top: int = 15,
) -> ProfileRow:
    """Run `calls` extractions under cProfile and return the top cumulative functions."""
    extract = EXECUTORS[executor]
    for _ in range(warmup):
        extract(case.document)
    observation = extract(case.document)
    samples = []
    for _ in range(calls):
        started = perf_counter()
        extract(case.document)
        samples.append((perf_counter() - started) * 1000)
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        for _ in range(calls):
            observation = extract(case.document)
    finally:
        profiler.disable()
        profiler.create_stats()
    # Profile entries are (call_count, primitive_count, total, cumulative, callers).
    entries = profiler.stats
    root = _function(*_label(extract))
    total = sum(
        float(entry[3]) for label, entry in entries.items() if _function(label[0], label[2]) == root
    )
    totals: dict[str, list[float]] = {}
    for (filename, _, name), entry in entries.items():
        key = _function(filename, name)
        if key == root:
            continue
        aggregated = totals.setdefault(key, [0.0, 0.0, 0.0])
        aggregated[0] += float(entry[0])
        aggregated[1] += float(entry[2])
        aggregated[2] += float(entry[3])
    ranked = sorted(totals.items(), key=lambda item: item[1][2], reverse=True)[:top]
    return {
        "case": case.name,
        "category": case.category,
        "executor": executor,
        "status": observation.status,
        "records": len(observation.records),
        "calls": calls,
        "warmup": warmup,
        "per_call_ms": round(total * 1000 / calls, 4),
        "unprofiled_ms_per_call": round(statistics.median(samples), 4),
        "top": [
            Hotspot(
                function=key,
                cumulative_ms_per_call=round(cumulative * 1000 / calls, 4),
                self_ms_per_call=round(own * 1000 / calls, 4),
                share=round(cumulative / total, 4) if total else 0.0,
                calls_per_call=round(count / calls, 3),
            )
            for key, (count, own, cumulative) in ranked
        ],
    }


def _label(extract: Callable[[Document], Observation]) -> tuple[str, str]:
    code = extract.__code__
    return code.co_filename, code.co_name


def _per_op_ms(operation: Callable[[], object], repeats: int = 1_000) -> float:
    """Median unprofiled cost of one call, to price the per-extraction fixed work."""
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000)
    return round(statistics.median(samples), 5)


def fixed_costs(case: Case) -> dict[str, float]:
    """Page-independent Rastro work repeated on every extraction, priced without cProfile."""
    mapping = case.document.mappings[0]
    recipe = Recipe(**mapping.model_dump(), require_evidence=True)
    schema = case.document.record_schema
    source = Source(url="https://example.com/benchmark")
    observed_at = datetime.now(UTC)
    validate_recipe(schema, recipe)
    schema_fingerprint(schema)
    return {
        "schema_fingerprint": _per_op_ms(lambda: schema_fingerprint(schema)),
        "validate_recipe_roundtrip": _per_op_ms(lambda: validate_recipe(schema, recipe)),
        "source_evidence_url": _per_op_ms(lambda: source.evidence_url),
        "evidence_model": _per_op_ms(
            lambda: Evidence(
                source_url="https://example.com/benchmark",
                observed_at=observed_at,
                method="css",
                locator=".product [match 0] >> h2::text",
                recipe_version=1,
            )
        ),
    }


def run(
    *,
    calls: int = 30,
    warmup: int = 5,
    cases: Sequence[str] = DEFAULT_CASES,
    top: int = 15,
) -> Report:
    if calls < 1 or not 0 <= warmup <= 100:
        raise ValueError("Use at least one call and 0-100 warmups")
    available = {case.name: case for case in load_cases()}
    unknown = [name for name in cases if name not in available]
    if unknown:
        raise ValueError(f"Unknown corpus cases: {', '.join(unknown)}")
    logger = logging.getLogger("rastro")
    disabled = logger.disabled
    # Match the recorded benchmark: completion logging is off for every executor.
    logger.disabled = True
    try:
        rows = [
            profile(executor, available[name], calls=calls, warmup=warmup, top=top)
            for name in cases
            for executor in EXECUTORS
        ]
    finally:
        logger.disabled = disabled
    return {
        "format_version": 1,
        "scope": "offline supplied-HTML extraction with explicit recipes, reconstructed per call",
        "created_at": datetime.now(UTC).isoformat(),
        "calls": calls,
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
        "unmeasured": ["cold process start", "network", "JavaScript rendering", "live inference"],
        "fixed_costs": fixed_costs(available["static"]),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "latency-profile.json")
    parser.add_argument("--calls", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--cases", nargs="+", default=list(DEFAULT_CASES))
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    report = run(calls=args.calls, warmup=args.warmup, cases=args.cases, top=args.top)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for row in report["cases"]:
        print(f"{row['case']:>9} {row['executor']:<20} {row['per_call_ms']:>8.3f} ms/call")
        for item in row["top"][:5]:
            print(
                f"    {item['share']:>6.1%} {item['cumulative_ms_per_call']:>8.3f} ms  "
                f"{item['function']}"
            )


if __name__ == "__main__":
    main()
