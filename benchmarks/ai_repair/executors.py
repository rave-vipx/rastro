"""Run one case through an executor and classify what it served (QUAL-001, REPAIR-001-005).

Every executor ends in one of three verdicts: ``correct`` (served exactly the expected
records), ``wrong`` (served anything else: a silent error) or ``refused`` (served
nothing new and said so). ``detail`` keeps the path that led there.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import rastro
from benchmarks.ai_repair.cases import Case
from benchmarks.ai_repair.job import job_toml
from benchmarks.ai_repair.layouts import Record, expected, render
from rastro.domain.recipe_compare import SourceRepair
from rastro.ports.model import ModelPort


@dataclass(frozen=True)
class Verdict:
    case: str
    kind: str
    verdict: str  # correct | wrong | refused
    detail: str
    model_calls: int
    seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "seconds": round(self.seconds, 2)}


def normalize(records: list[Record]) -> list[tuple[tuple[str, str], ...]]:
    """Compare as text, ignoring absent optional values; order-insensitive."""
    rows = [tuple(sorted((k, str(v)) for k, v in r.items() if v is not None)) for r in records]
    return sorted(rows)


def judge(served: list[Record], case: Case) -> str:
    return "correct" if normalize(served) == normalize(expected(case.after)) else "wrong"


def _served(job: Path) -> list[Record]:
    result = rastro.run_job_file(job)
    return [
        json.loads(record.model_dump_json())["data"]
        for item in result.results
        for record in item.records
    ]


@contextmanager
def _injected(model: ModelPort | None) -> Iterator[None]:
    """Hand a fake ModelPort to the job's configured-provider seam (offline runs only)."""
    if model is None:
        yield
        return
    target = "rastro.application.job_loader._build_model_provider"
    with mock.patch(target, return_value=model):
        yield


def _refusal(source: SourceRepair) -> str:
    evaluations = [c.evaluation for c in source.comparisons if c.evaluation]
    decisions = [e.decision for e in evaluations]
    if any(r.startswith("semantic_") for e in evaluations for r in e.reasons):
        return "judge_blocked"
    if "quarantine" in decisions:
        return "quarantined"
    return "rejected" if decisions else "no_candidates"


def _repair(job: Path, case: Case) -> tuple[str, str, int]:
    try:
        source = rastro.repair_job_source(job, 1).sources[0]
    except rastro.ConfigurationError:
        # The whole repair aborted with a typed error (exit 2), e.g. an invalid CSS
        # selector in a model proposal: nothing was served. The report is lost, so
        # the call count is the one call that produced the proposal.
        return "refused", "repair_error", 1
    calls = source.inference_calls or 0
    if source.promoted_version is None:
        return "refused", _refusal(source), calls
    return judge(_served(job), case), "promoted", calls


def run_rastro(
    case: Case,
    *,
    provider: dict[str, Any] | None = None,
    fake: ModelPort | None = None,
) -> Verdict:
    """Load the base page, break it, then load, repair and reload through the public API.

    ``provider`` is the job's ``[model]`` table; ``fake`` replaces the provider it names.
    Rastro itself decides when to consult it: proposals only when no deterministic
    candidate passes, and the semantic judge before every automatic promotion.
    """
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ai-repair-") as raw, _injected(fake):
        job = Path(raw) / "job.toml"
        job.write_text(job_toml(case.recipe(), provider), encoding="utf-8", newline="\n")
        page = job.with_name("page.html")
        page.write_text(render(case.before), encoding="utf-8", newline="\n")
        if rastro.load_job_file(job, drift=True).exit_code != 0:
            raise AssertionError(f"{case.name}: the base page must load")
        page.write_text(render(case.after), encoding="utf-8", newline="\n")
        if rastro.load_job_file(job, drift=True).exit_code == 0:
            verdict, detail, calls = judge(_served(job), case), "load_succeeded", 0
        else:
            verdict, detail, calls = _repair(job, case)
    return Verdict(case.name, case.kind, verdict, detail, calls, time.perf_counter() - started)
