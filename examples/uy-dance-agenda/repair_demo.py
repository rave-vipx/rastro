"""Clock-pinned real CLI demo on a disposable sample (REPAIR-001-006)."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
from contextlib import ExitStack, redirect_stdout
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from rastro.cli import main
from rastro.domain.jobs import parse_job
from rastro.mocklab.mutations.html_content import HtmlChangeClasses, HtmlChangeLabel
from rastro.mocklab.mutations.html_structure import HtmlWrapElement
from rastro.mocklab.mutations.plan import MutationPlan


def command(*args: str) -> tuple[int, dict[str, Any]]:
    """Keep full CLI JSON in memory; print only the fields used by the demo assertions."""
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(list(args))
    print("rastro " + " ".join(args))
    payload = json.loads(output.getvalue())
    if args[:2] == ("job", "load"):
        drift = ",".join(item["verdict"] for item in payload.get("drift", []))
        fields = sorted({s["field"] for d in payload.get("drift", []) for s in d["signals"]})
        print(
            f"exit={code} status={payload['job']['status']} stale={len(payload['stale_sources'])}"
            f" drift={drift} fields={','.join(fields)}"
        )
    elif args[:2] == ("recipe", "repair"):
        checks = [c["evaluation"] for s in payload["sources"] for c in s["comparisons"]]
        print(
            f"exit={code} decisions={','.join(c['decision'] for c in checks)}"
            f" consistency={','.join(c['indicators']['verdict'] for c in checks)}"
        )
    elif args[:2] == ("recipe", "compare"):
        print(
            f"exit={code} schema_version={payload['schema_version']} "
            f"versions={payload['previous_version']}->{payload['candidate_version']} "
            f"changed={','.join(payload['changed_fields'])}"
        )
    else:
        print(f"exit={code} state={payload['state']} version={payload['version']}")
    return code, payload


def run_demo(folder: Path) -> None:
    sample = Path(__file__).parent / "sample"
    shutil.copytree(sample, folder, ignore=shutil.ignore_patterns("*.sqlite", "repair-report-*"))
    job = parse_job((folder / "job.toml").read_text(), format="toml").model_dump(mode="json")
    job.update(recipe_memory="recipes.sqlite", repair={"auto_promote": True, "min_records": 5})
    path = folder / "job.json"
    path.write_text(json.dumps(job))
    previous_cwd = Path.cwd()
    os.chdir(folder)
    try:
        # Patch existing observation seams only; the engine keeps its real clock by default.
        observed, ticks = datetime(2026, 11, 7, 12, tzinfo=UTC), count()
        with ExitStack() as stack:
            for module in (
                "rastro.application.extraction",
                "rastro.application.service",
                "rastro.application.memory",
                "rastro.application.outcomes",
                "rastro.application.intelligence",
                "rastro.cli_recipe",
            ):
                clock = stack.enter_context(patch(f"{module}.datetime", wraps=datetime))
                clock.now.side_effect = lambda tz: observed + timedelta(microseconds=next(ticks))
            _sequence(path, job)
    finally:
        os.chdir(previous_cwd)


def _sequence(path: Path, job: dict[str, Any]) -> None:
    assert command("job", "load", "job.json", "--drift")[0] == 0
    html = Path("sodre-cartelera.html")
    original = html.read_text()
    changed = MutationPlan(
        seed=42,
        operators=(
            HtmlWrapElement(selector="h2.cyg--display-3", wrapper_tag="section"),
            HtmlChangeClasses(selector="h2.cyg--display-3", rename={"cyg--display-3": "new-title"}),
        ),
    ).apply(original)
    html.write_text(changed.document)
    print("Mock Lab: moved and renamed title nodes (seed=42)")
    assert command("job", "load", "job.json", "--drift")[0] == 3
    assert command("recipe", "repair", "job.json", "--source", "1")[0] == 3
    job["repair"]["min_records"] = 4
    path.write_text(json.dumps(job))
    print("Configured repair.min_records=4 (was 5)")
    code, report = command("recipe", "repair", "job.json", "--source", "1")
    assert code == 0
    version = next(
        c["candidate_version"]
        for c in report["sources"][0]["comparisons"]
        if c["evaluation"]["decision"] == "promote"
    )
    assert command("job", "load", "job.json", "--drift")[0] == 0
    assert (
        command(
            "recipe", "compare", "job.json", "--from-version", "1", "--to-version", str(version)
        )[0]
        == 0
    )
    assert command("recipe", "rollback", "job.json", "--version", "1")[0] == 0
    html.write_text(original)
    assert command("job", "load", "job.json", "--drift")[0] == 0
    seller = Path("tickantel-evento.html")
    body = seller.read_text()
    replacements = tuple(
        HtmlChangeLabel(
            selector='script[type="application/ld+json"]',
            match=f'"startDate": "{date}"',
            text='"startDate": "2026-11-01T10:00:00-03:00"',
        )
        for date in re.findall(r'"startDate": "([^"]+)"', body)
    )
    seller.write_text(MutationPlan(seed=42, operators=replacements).apply(body).document)
    print("Mock Lab: valid sale date placed in event-date paths (seed=42)")
    code, report = command("recipe", "repair", "job.json", "--source", "2")
    assert code == 1
    assert any(
        c["evaluation"]["indicators"]["verdict"] == "inconsistent"
        for c in report["sources"][0]["comparisons"]
    )
    print("All assertions passed; temporary dataset retained 6 records; external spend=$0")
    from rastro.adapters.sqlite_dataset import SQLiteDatasetStore

    assert len(SQLiteDatasetStore("agenda.sqlite").records("uy_dance_agenda")) == 6


if __name__ == "__main__":
    with TemporaryDirectory(prefix="rastro-repair-") as temporary:
        run_demo(Path(temporary) / "sample")
