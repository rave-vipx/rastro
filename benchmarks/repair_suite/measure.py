"""Measure automatic repair on realistic breakage through the public CLI (REPAIR-001-005, DIFF-002).

Usage: python benchmarks/repair_suite/measure.py [--case page:mutation ...] [--output results.json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from mutations import CASES  # type: ignore[import-not-found]  # noqa: E402

OUTCOMES = (
    "promoted_correct",
    "promoted_wrong",
    "quarantined",
    "rejected",
    "no_candidates",
    "not_broken",
    "silent_wrong",
    "held",
)


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    """Run the public CLI in a child process (isolated event loops, real exit codes)."""
    code = "import sys; from rastro.cli import main; sys.exit(main(sys.argv[1:]))"
    done = subprocess.run(
        [sys.executable, "-c", code, *args], capture_output=True, text=True, check=False
    )
    text = done.stdout.strip()
    return done.returncode, json.loads(text) if text.startswith("{") else {}


def stored_rows(folder: Path, dataset: str) -> dict[str, dict[str, Any]]:
    db = sqlite3.connect(folder / "data.sqlite")
    try:
        rows = db.execute("select payload from dataset_records where dataset = ?", (dataset,))
        payloads = [json.loads(r[0]) for r in rows.fetchall()]
    finally:
        db.close()
    return {p["fields"]["performance_id"]: p["fields"] for p in payloads}


def matches(rows: dict[str, dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    """Exactly the expected records, each with every expected field equal."""
    if set(rows) != {e["performance_id"] for e in expected}:
        return False
    return all(rows[e["performance_id"]].get(k) == v for e in expected for k, v in e.items())


def decisions(report: dict[str, Any]) -> list[str]:
    return [
        c["evaluation"]["decision"]
        for source in report.get("sources", [])
        for c in source.get("comparisons", [])
    ]


def classify(repair_code: int, report: dict[str, Any], reloaded: bool, correct: bool) -> str:
    found = decisions(report)
    if reloaded:
        return "promoted_correct" if correct else "promoted_wrong"
    if not found:
        return "no_candidates"
    return "quarantined" if "quarantine" in found else "rejected"


def run_case(page: str, name: str) -> dict[str, Any]:
    case = next(c for c in CASES[page] if c.name == name)
    source = HERE / "cases" / page
    expected = json.loads((source / "expected.json").read_text(encoding="utf-8"))
    config = tomllib.loads((source / "job.toml").read_text(encoding="utf-8"))
    model_calls = int("model" in config)  # no model is configured; any other use would show here
    started = time.perf_counter()
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="repair-suite-") as raw:
        folder = Path(raw)
        for file in ("job.toml", "page.html"):
            shutil.copy(source / file, folder / file)
        os.chdir(folder)
        try:
            dataset = config["dataset"]["name"]
            assert cli("job", "load", "job.toml", "--drift")[0] == 0, "base page must load"
            assert matches(stored_rows(folder, dataset), expected), "base rows must match"
            page_file = folder / "page.html"
            page_file.write_text(case.transform(page_file.read_text(encoding="utf-8")), "utf-8")
            code, loaded = cli("job", "load", "job.toml", "--drift")
            held = any(
                s.get("consistency", {}).get("verdict") == "inconsistent"
                for s in loaded.get("sources", [])
            )
            if code == 3 and held and matches(stored_rows(folder, dataset), expected):
                outcome = "held"
            elif code == 0:
                ok = matches(stored_rows(folder, dataset), expected)
                outcome = "not_broken" if ok else "silent_wrong"
            else:
                repair_code, report = cli("recipe", "repair", "job.toml")
                reloaded = repair_code == 0 and cli("job", "load", "job.toml")[0] == 0
                correct = reloaded and matches(stored_rows(folder, dataset), expected)
                outcome = classify(repair_code, report, reloaded, correct)
        finally:
            os.chdir(previous)
    return {
        "page": page,
        "mutation": name,
        "kind": case.kind,
        "outcome": outcome,
        "seconds": round(time.perf_counter() - started, 2),
        "model_calls": model_calls,
    }


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="page:mutation; default is every case")
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    args = parser.parse_args(argv)
    names = args.case or [f"{p}:{c.name}" for p, cases in CASES.items() for c in cases]
    results = [run_case(*name.split(":", 1)) for name in names]
    counts = {o: sum(r["outcome"] == o for r in results) for o in OUTCOMES}
    by_kind = {
        kind: {o: sum(r["outcome"] == o and r["kind"] == kind for r in results) for o in OUTCOMES}
        for kind in ("structural", "trap")
    }
    args.output.write_text(
        json.dumps({"cases": results, "counts": counts, "by_kind": by_kind}, indent=1) + "\n",
        encoding="utf-8",
    )
    for r in results:
        print(
            f"{r['page']:<10} {r['mutation']:<28} {r['kind']:<10} "
            f"{r['outcome']:<17} {r['seconds']:>6.2f}s  model_calls={r['model_calls']}"
        )
    print(json.dumps(by_kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
