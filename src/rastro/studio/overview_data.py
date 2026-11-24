"""Read-only state behind the Projects and Sources views (N5): one job's dataset status,
run metrics, open drift, waiting candidates and Studio's run log, reduced to plain statuses.

Every code shown comes from Rastro's own vocabulary (``KNOWN_CODES``); anything else read
from a file becomes ``unknown``, even when it is shaped like a code. Nothing here creates a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.active_recipes import source_key
from rastro.application.job_loader import resolve_job_path
from rastro.application.job_maintenance import current_source_urls
from rastro.domain.dataset import SourceStatus
from rastro.domain.health import OpenDrift
from rastro.domain.issue_codes import KNOWN_CODES
from rastro.domain.jobs import Job
from rastro.domain.lifecycle import RecipeState
from rastro.domain.metrics import RunMetric
from rastro.domain.models import Outcome
from rastro.domain.run_report import RunReport
from rastro.studio.overview_say import RANK, hint, light
from rastro.studio.run_log import LoggedRun, LoggedSource, read_runs

FAILED = frozenset(o.value for o in Outcome) - {"success", "unchanged", "partial_success"}
PROBLEMS = frozenset({"failed", "stale"})
CAP_ENDINGS = ("_cap", "max_pages", "page_limit", "depth_limit", "budget")
MAX_WARNINGS = 10


def code(value: object) -> str | None:
    """A Rastro code as is; other text from a file becomes ``unknown``; None stays None."""
    if value is None:
        return None
    return value if isinstance(value, str) and value in KNOWN_CODES else "unknown"


@dataclass(frozen=True)
class Candidate:
    url: str
    at: datetime


@dataclass
class JobState:
    """What Studio knows about one job without running it."""

    job: Job
    urls: tuple[str, ...]
    statuses: dict[str, SourceStatus] = field(default_factory=dict)
    metrics: tuple[RunMetric, ...] = ()
    drift: dict[str, OpenDrift] = field(default_factory=dict)
    runs: tuple[LoggedRun, ...] = ()
    candidates: list[Candidate] = field(default_factory=list)
    records: int = 0
    # Configured URL -> the final URL a redirect stored it under (from the run metrics).
    redirects: dict[str, str] = field(default_factory=dict)
    # The run report of the latest logged load, if Studio kept one.
    report: RunReport | None = None


def _dataset(state: JobState, root: Path, path: Path) -> None:
    spec = state.job.dataset
    if spec is None or spec.backend != "sqlite":
        return  # A PostgreSQL dataset keeps no metrics; its sources read as "never".
    file = resolve_job_path(spec.path, base_dir=path.parent)
    if not file.is_relative_to(root) or not file.is_file():
        return
    store = SQLiteDatasetStore(file, limits=state.job.limits)
    if spec.name not in store.dataset_names():
        return
    state.statuses = {s.source_url: s for s in store.source_status(spec.name)}
    state.metrics = store.run_metrics(spec.name)
    # Every metric in append order, direct loads included, so the latest attempt wins.
    state.redirects = {(m.requested_url or m.source_url): m.source_url for m in state.metrics}
    state.records = len(store.records(spec.name))
    monitor = SQLiteDriftMonitor(file, limits=state.job.limits)
    state.drift = {d.source_url: d for d in monitor.open_drift(spec.name)}


def _candidates(state: JobState, root: Path, path: Path) -> None:
    if state.job.recipe_memory is None:
        return
    memory = resolve_job_path(state.job.recipe_memory, base_dir=path.parent)
    if not memory.is_relative_to(root) or not memory.is_file():
        return
    repository = SQLiteRecipeRepository(memory, read_only=True)
    for item, url in zip(state.job.sources, state.urls, strict=True):
        history = repository.history(source_key(state.job, item))
        waiting = {v.version for v in history.versions if v.state == RecipeState.CANDIDATE}
        events = [e for e in history.events if e.version in waiting]
        state.candidates += [Candidate(url, e.at) for e in events if e.to_state == "candidate"]


def job_state(root: Path, path: Path, job: Job) -> JobState:
    state = JobState(job=job, urls=current_source_urls(job))
    _dataset(state, root, path)
    _candidates(state, root, path)
    state.runs = read_runs(root, path)
    state.report = next((r.report for r in reversed(state.runs) if r.report), None)
    return state


def _logged(state: JobState, url: str) -> tuple[LoggedSource, datetime] | None:
    for run in reversed(state.runs):
        if run.kind == "load":
            for item in run.sources:
                if item.url == url:
                    return item, run.at
    return None


def _latest_metric(state: JobState, url: str) -> RunMetric | None:
    return next((m for m in reversed(state.metrics) if m.source_url == url), None)


def capped(item: LoggedSource) -> bool:
    return (item.cap is not None and item.read >= item.cap) or any(
        i.code.endswith(CAP_ENDINGS) for i in item.issues
    )


def _kind(
    status: SourceStatus | None,
    metric: RunMetric | None,
    item: LoggedSource | None,
    warnings: list[str],
) -> tuple[str, str | None]:
    # A reached cap leaves a partial snapshot, which the dataset marks stale: say "capped".
    cap = metric.cap if metric is not None else None
    if status is not None and status.stale and not (cap and status.reason == "partial_snapshot"):
        kind = "failed" if status.reason in FAILED else "stale"
        return kind, code(status.detail or status.reason) or "unknown"
    if cap is not None:  # the engine records caps for every load, CLI runs included
        return "capped", code(cap)
    if metric is None and item is not None and capped(item):  # no metrics: PostgreSQL
        cap_codes = [i.code for i in item.issues if i.code.endswith(CAP_ENDINGS)]
        return "capped", code((cap_codes or ["cap_reached"])[0])
    if warnings:
        return "warning", warnings[0]
    return "ok", None


def source_row(state: JobState, url: str) -> dict[str, Any]:
    stored = state.redirects.get(url, url)  # the dataset keys a redirected source by its end
    status = state.statuses.get(stored)
    logged = _logged(state, stored)
    item = logged[0] if logged else None
    metric = _latest_metric(state, stored)
    issues = item.issues if item else ()
    warnings = [str(code(i.code)) for i in issues if not i.code.endswith(CAP_ENDINGS)]
    warnings += ["open_drift"] if stored in state.drift else []
    warnings += ["quarantined_candidate"] if any(c.url == url for c in state.candidates) else []
    warnings = list(dict.fromkeys(warnings))[:MAX_WARNINGS]
    if status is None and item is None and metric is None:
        kind, issue = "never", None
    else:
        kind, issue = _kind(status, metric, item, warnings)
    moments = [
        status.last_attempt if status else None,
        logged[1] if logged else None,
        metric.finished_at if metric else None,
    ]
    last = next((m for m in moments if m is not None), None)
    say = hint(state.report, url)
    return {
        "url": url,
        "status": kind,
        "light": light(kind, say),
        "say": say,
        "issue_code": issue,
        "last_fetch": last.isoformat() if last else None,
        **_counts(metric, item),
        "warnings": warnings,
    }


def _counts(metric: RunMetric | None, item: LoggedSource | None) -> dict[str, int | None]:
    """Engine counts first; ``None`` when nothing recorded them (never a made-up 0)."""
    if metric is not None:
        cap = metric.cap_value if metric.cap else None
        return {"read": metric.records, "rejected": metric.rejected, "cap": cap}
    return {
        "read": item.read if item else None,
        "rejected": None,  # Studio's run log never knew rejections
        "cap": item.cap if item and capped(item) else None,
    }


def display_name(raw: str) -> str:
    """``agenda_fiestas`` reads as ``Agenda fiestas``: a job has no display name of its own."""
    text = " ".join(raw.replace("_", " ").replace("-", " ").split())
    return text[:1].upper() + text[1:] if text else raw


def sources(state: JobState) -> list[dict[str, Any]]:
    return [source_row(state, url) for url in state.urls]


def project(rel: str, state: JobState) -> dict[str, Any]:
    rows = sources(state)
    attempted = [r for r in rows if r["status"] != "never"]
    if not attempted:
        health = "unknown"
    elif all(r["status"] in PROBLEMS for r in attempted):
        health = "failing"
    elif any(r["status"] != "ok" for r in attempted):
        health = "warnings"
    else:
        health = "ok"
    lights = [r["light"] for r in rows]
    moments = [r["last_fetch"] for r in rows if r["last_fetch"]]
    last = max(moments, key=datetime.fromisoformat) if moments else None
    spec = state.job.dataset
    return {
        "file": rel,
        "name": display_name(spec.name if spec else Path(rel).stem),
        "dataset": spec.name if spec else None,
        "health": health,
        "light": max(lights, key=RANK.__getitem__) if lights else "grey",
        "last_run": last,
        "records": state.records,
        "sources_total": len(rows),
        "sources_with_problems": sum(r["status"] in PROBLEMS for r in rows),
    }
