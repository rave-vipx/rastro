"""The Alerts view (N5): dated, content-free alerts for one job, newest first.

Failures come from run metrics and the dataset's source status (CLI runs included); open
caps from run metrics too; drift from the dataset; waiting candidates from recipe memory;
warnings and refused promotions from Studio's run log (caps only for sources the engine has
no metrics for). Each alert is a kind and a Rastro code; the page writes
the sentence. A field name is shown only when the job declares it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rastro.domain.run_report import RunReport
from rastro.studio.overview_data import CAP_ENDINGS, FAILED, JobState, capped, code

MAX_ALERTS = 100


def _alert(
    at: datetime,
    kind: str,
    source: str | None,
    issue: str | None,
    action: str,
    field: str | None = None,
) -> dict[str, Any]:
    return {
        "at": at,
        "kind": kind,
        "source": source,
        "code": issue or "unknown",
        "field": field,
        "action": action,
    }


def _failures(state: JobState) -> list[dict[str, Any]]:
    found, seen = [], set()
    for metric in state.metrics:
        if metric.outcome not in FAILED:
            continue
        status = state.statuses.get(metric.source_url)
        latest = status is not None and status.last_attempt == metric.finished_at
        issue = code(status.detail) if latest and status and status.detail else code(metric.outcome)
        seen.add((metric.source_url, metric.finished_at))
        found.append(_alert(metric.finished_at, "failure", metric.source_url, issue, "retry"))
    for url, status in state.statuses.items():
        if status.stale and status.reason in FAILED and (url, status.last_attempt) not in seen:
            issue = code(status.detail or status.reason)
            found.append(_alert(status.last_attempt, "failure", url, issue, "retry"))
    return found


def _caps(state: JobState) -> list[dict[str, Any]]:
    return [
        _alert(m.finished_at, "cap", m.source_url, code(m.cap), "raise_cap")
        for m in state.metrics
        if m.cap is not None
    ]


def _logged(state: JobState) -> list[dict[str, Any]]:
    declared = {f.name for f in state.job.fields}
    measured = {m.source_url for m in state.metrics}
    found = []
    for run in state.runs:
        for item in run.sources:
            if run.kind == "promote_refused":
                found.append(
                    _alert(run.at, "promote_refused", item.url, "promotion_refused", "details")
                )
                continue
            if capped(item) and item.url not in measured:
                found.append(_alert(run.at, "cap", item.url, "cap_reached", "raise_cap"))
            for issue in item.issues:
                if issue.code.endswith(CAP_ENDINGS):
                    continue
                name = issue.field if issue.field in declared else None
                found.append(_alert(run.at, "warning", item.url, code(issue.code), "details", name))
    return found


def report_alerts(report: RunReport) -> list[dict[str, Any]]:
    """Alerts for one run report: the same codes the report holds, so texts never disagree."""
    found = []
    for item in report.sources:
        source = f"https://{item.host}{item.path}" if item.host else None
        at = report.completed_at
        repair = item.repair
        if repair is not None and repair.decision == "quarantined":
            code_ = repair.promotion_refused or "quarantined_candidate"
            found.append(_alert(at, "quarantine", source, code_, "fix"))
        elif repair is not None and repair.promotion_refused:
            found.append(_alert(at, "promote_refused", source, repair.promotion_refused, "details"))
        if repair is not None and repair.drift in ("elevated", "high"):
            found.append(_alert(at, "drift", source, f"drift_{repair.drift}", "fix"))
    return found


def alerts(state: JobState, report: RunReport | None = None) -> list[dict[str, Any]]:
    items = _failures(state) + _caps(state) + _logged(state)
    if report is not None:
        items += report_alerts(report)
    items += [
        _alert(d.observed_at, "drift", url, code(d.verdict), "fix")
        for url, d in state.drift.items()
    ]
    items += [
        _alert(c.at, "quarantine", c.url, "quarantined_candidate", "fix") for c in state.candidates
    ]
    items.sort(key=lambda a: a["at"], reverse=True)
    return [{**a, "at": a["at"].isoformat()} for a in items[:MAX_ALERTS]]
