"""DIFF-002 offline product-task comparison."""

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from unittest.mock import patch

from benchmarks.product_task.baseline import ROOT, Source, load_source, read_records, sources
from benchmarks.product_task.rastro_path import run as run_rastro

HERE = Path(__file__).resolve().parent
HUMAN_STEPS = [
    "Inspect the two sample sources and define the agenda schema and stable identity.",
    "Write source selectors/JSON pointers; validate records and inspect the database query.",
    "Inspect the third source, add its mapping and run the load again.",
    "On missing_data inspect the changed HTML; change record_selector from .dance to .performance.",
    "Re-run the failed source and verify the price update and unchanged row count.",
]


def score(actual: list[dict[str, str]], expected: list[dict[str, str]]) -> dict[str, int]:
    """Wrong accepted rows count once each; missing rows are reported separately."""
    truth = {row["performance_id"]: row for row in expected}
    seen: Counter[str] = Counter()
    silent = 0
    for row in actual:
        key = row["performance_id"]
        seen[key] += 1
        silent += int(row != truth.get(key) or seen[key] > 1)
    return {"silent_errors": silent, "missing_records": len(truth.keys() - seen.keys())}


def counted_lines(path: Path) -> int:
    """Physical nonblank, non-comment lines; docstrings and JSON punctuation count."""
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def phase(
    database: Path,
    name: str,
    inputs: list[Source],
    expected: list[dict[str, str]],
) -> dict[str, Any]:
    started = perf_counter()
    statuses = [load_source(database, source) for source in inputs]
    elapsed = perf_counter() - started
    records = read_records(database)
    quality = score(records, expected)
    failed = any(status != "success" for status in statuses)
    stale = quality["silent_errors"] if failed else 0
    return {
        "name": name,
        "status": next((status for status in statuses if status != "success"), "success"),
        "source_statuses": statuses,
        "wall_seconds": elapsed,
        "machine_minutes": elapsed / 60,
        "records": len(records),
        "stale_records": stale,
        **quality,
        "dataset_wrong_records": quality["silent_errors"],
        "silent_errors": quality["silent_errors"] - stale,
    }


def run_phases(database: Path) -> tuple[list[dict[str, Any]], float, dict[str, str], int]:
    """Use a fresh database; exclude scoring and human work from phase timings."""
    if database.exists():
        raise ValueError("Measurement requires a new database")
    oracle = json.loads((HERE / "fixtures/expected.json").read_text(encoding="utf-8"))
    initial = oracle["initial"]
    expanded = [*initial, oracle["third"]]
    repaired_truth = [*initial, oracle["repaired_third"]]
    configs = sources()
    network_requests = 0

    def forbidden_network(*args: object, **kwargs: object) -> None:
        nonlocal network_requests
        network_requests += 1
        raise RuntimeError("Offline benchmark attempted network access")

    with (
        patch("socket.socket.connect", forbidden_network),
        patch("socket.socket.connect_ex", forbidden_network),
        patch("socket.getaddrinfo", forbidden_network),
    ):
        phases = [phase(database, "initial_two", configs[:2], initial)]
        phases.append(phase(database, "add_third", configs[2:], expanded))
        phases.append(phase(database, "repeat", configs, expanded))
        broken = configs[2].model_copy(
            update={"path": "benchmarks/product_task/fixtures/third-broken.html"}
        )
        phases.append(phase(database, "broken", [broken], repaired_truth))
        started = perf_counter()
        repair = json.loads((HERE / "fixtures/repair.json").read_text(encoding="utf-8"))
        fixed = broken.model_copy(update={"mapping": broken.mapping.model_copy(update=repair)})
        phases.append(phase(database, "repaired", [fixed], repaired_truth))
        repair_seconds = perf_counter() - started
    return phases, repair_seconds, repair, network_requests


def evidence() -> dict[str, Any]:
    hashes = [
        *HERE.rglob("*.py"),
        *HERE.joinpath("baseline").glob("*.json"),
        *HERE.joinpath("rastro").glob("*.json"),
        *(ROOT / "src/rastro").rglob("*.py"),
        *HERE.joinpath("fixtures").glob("*.json"),
        *HERE.joinpath("fixtures").glob("*.html"),
        ROOT / "benchmarks/baseline.py",
        ROOT / "benchmarks/contracts.py",
        ROOT / "uv.lock",
        *(ROOT / "examples/uy-dance-agenda/sample").glob("*.html"),
    ]
    return {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "packages": {
                name: version(name) for name in ("scrapling", "pydantic", "lxml", "rastro-engine")
            },
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "git_dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ).strip()
            ),
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(hashes)
        },
    }


def baseline_metrics(
    phases: list[dict[str, Any]],
    repair_seconds: float,
    repair: dict[str, str],
    network_requests: int,
) -> dict[str, Any]:
    files = [HERE / "baseline/agenda.py", HERE / "baseline/sources.json"]
    line_counts = {str(path.relative_to(ROOT)): counted_lines(path) for path in files}
    return {
        "phases": phases,
        "network_requests": network_requests,
        "model_calls": 0,
        "site_specific_lines": sum(line_counts.values()),
        "line_counts": line_counts,
        "one_time_loader_lines": counted_lines(HERE / "baseline/__init__.py"),
        "shared_extractor_lines": counted_lines(ROOT / "benchmarks/baseline.py"),
        "shared_contract_lines": counted_lines(ROOT / "benchmarks/contracts.py"),
        "initial_machine_minutes": phases[0]["machine_minutes"],
        "repair": {
            "scripted_wall_seconds": repair_seconds,
            "config_edits": sum(
                value != getattr(sources()[2].mapping, key) for key, value in repair.items()
            ),
            "config_lines": counted_lines(HERE / "fixtures/repair.json"),
            "human_minutes": None,
            "human_steps": HUMAN_STEPS[3:],
        },
        "human_steps": HUMAN_STEPS,
        "human_minutes": None,
        "cost": {
            "initial_model_calls": 0,
            "recurring_model_calls": 0,
            "initial_network_requests": 0,
            "recurring_network_requests": 0,
            "external_service_spend": 0,
            "development_agent_usage": "not instrumented",
        },
    }


def measure(database: Path) -> dict[str, Any]:
    """Measure a fresh database; human effort remains explicitly unmeasured."""
    phases, repair_seconds, repair, requests = run_phases(database)
    return {
        "requirement": "DIFF-002",
        **evidence(),
        "baseline": baseline_metrics(phases, repair_seconds, repair, requests),
        "rastro": run_rastro(database.parent / "rastro"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="rastro-product-task-") as directory:
        report = measure(Path(directory) / "baseline.sqlite")
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
