"""DIFF-002 real CLI workflow over the baseline's exact offline inputs."""

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from benchmarks.product_task.rastro.loader import stage_candidate
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HUMAN_STEPS = [
    "Inspect the two sample sources; declare fields, identity and recipes in job.json.",
    "Run job validate and preview; inspect values and provenance; run job load and query SQLite.",
    "Inspect the third source, add its source/recipe block and validate, preview and load.",
    "On missing_data inspect HTML and try recipe repair; stage the manual selector fix.",
    "Review the promotion report, reload the source and verify the updated database.",
]


def command(*args: str) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(list(args))
    return code, json.loads(output.getvalue())


def records(folder: Path) -> list[dict[str, Any]]:
    return [row.fields for row in SQLiteDatasetStore(folder / "agenda.sqlite").records("agenda")]


def write_job(folder: Path, job: dict[str, Any], *, memory: bool) -> Path:
    path = folder / "job.json"
    payload = {**job, **({"recipe_memory": "recipes.sqlite"} if memory else {})}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def phase(
    folder: Path, name: str, job: dict[str, Any], expected: list[dict[str, str]]
) -> dict[str, Any]:
    from benchmarks.product_task.measure import score

    path = write_job(folder, job, memory=False)
    started = perf_counter()
    checks = []
    if name in ("initial_two", "add_third"):
        for operation in ("validate", "preview"):
            code, _ = command(
                "job",
                operation,
                str(path),
                *(("--output", "json") if operation == "preview" else ()),
            )
            checks.append({"command": operation, "exit_code": code})
            if code:
                raise RuntimeError(f"Rastro {operation} failed")
    write_job(folder, job, memory=True)
    code, payload = command("job", "load", str(path))
    elapsed = perf_counter() - started
    quality = score(records(folder), expected)
    statuses = [source["status"] for source in payload["sources"]]
    stale = quality["silent_errors"] if code else 0
    return {
        "name": name,
        "status": next((s for s in statuses if s != "success"), "success"),
        "source_statuses": statuses,
        "wall_seconds": elapsed,
        "machine_minutes": elapsed / 60,
        "records": len(records(folder)),
        "stale_records": stale,
        **quality,
        "dataset_wrong_records": quality["silent_errors"],
        "silent_errors": quality["silent_errors"] - stale,
        "preflight": checks,
    }


def run(folder: Path) -> dict[str, Any]:
    from benchmarks.product_task.measure import counted_lines

    folder = folder.resolve()
    folder.mkdir()  # Never reuse or overwrite a prior integration database.
    job = json.loads((HERE / "rastro/job.json").read_text())
    for source in job["sources"]:
        candidates = [ROOT / "examples/uy-dance-agenda/sample", HERE / "fixtures"]
        original = next(
            base / source["path"] for base in candidates if (base / source["path"]).is_file()
        )
        shutil.copyfile(original, folder / source["path"])
    oracle = json.loads((HERE / "fixtures/expected.json").read_text())
    initial = oracle["initial"]
    expanded = [*initial, oracle["third"]]
    repaired = [*initial, oracle["repaired_third"]]
    sources = job["sources"]
    requests = 0

    def forbidden_network(*args: object, **kwargs: object) -> None:
        nonlocal requests
        requests += 1
        raise RuntimeError("Offline benchmark attempted network access")

    with (
        patch("socket.socket.connect", forbidden_network),
        patch("socket.socket.connect_ex", forbidden_network),
        patch("socket.getaddrinfo", forbidden_network),
        patch("curl_cffi.Curl.perform", forbidden_network),
        patch("curl_cffi.AsyncCurl.add_handle", forbidden_network),
    ):
        phases = [phase(folder, "initial_two", {**job, "sources": sources[:2]}, initial)]
        phases.append(phase(folder, "add_third", {**job, "sources": sources[2:]}, expanded))
        phases.append(phase(folder, "repeat", job, expanded))
        shutil.copyfile(HERE / "fixtures/third-broken.html", folder / "third.html")
        third = {**job, "sources": sources[2:]}
        phases.append(phase(folder, "broken", third, repaired))
        started = perf_counter()
        _, generated = command("recipe", "repair", str(folder / "job.json"), "--source", "1")
        candidate_version, edit_count = stage_candidate(
            folder / "job.json", HERE / "rastro/repair.json"
        )
        code, repair = command(
            "recipe",
            "promote",
            str(folder / "job.json"),
            "--source",
            "1",
            "--version",
            str(candidate_version),
        )
        if code:
            raise RuntimeError(f"Rastro promotion failed: {repair}")
        phases.append(phase(folder, "repaired", third, repaired))
        repair_seconds = perf_counter() - started
    promoted = next(
        c for c in repair["sources"][0]["comparisons"] if c["evaluation"]["decision"] == "promote"
    )
    line_counts = {
        "benchmarks/product_task/rastro/job.json": counted_lines(HERE / "rastro/job.json")
    }
    return {
        "phases": phases,
        "network_requests": requests,
        "model_calls": sum(s["inference_calls"] for s in generated["sources"]),
        "site_specific_lines": sum(line_counts.values()),
        "line_counts": line_counts,
        "one_time_loader_lines": counted_lines(HERE / "rastro/loader.py"),
        "shared_runtime_lines": sum(counted_lines(p) for p in (ROOT / "src/rastro").rglob("*.py")),
        "initial_machine_minutes": phases[0]["machine_minutes"],
        "repair": {
            "scripted_wall_seconds": repair_seconds,
            "config_edits": edit_count,
            "config_lines": counted_lines(HERE / "rastro/repair.json"),
            "generation_outcome": generated["sources"][0]["outcome"],
            "human_minutes": None,
            "human_steps": HUMAN_STEPS[3:],
            "decision": promoted["evaluation"]["decision"],
            "evaluation": promoted["evaluation"],
            "comparisons": repair["sources"][0]["comparisons"],
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
