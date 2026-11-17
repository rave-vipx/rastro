"""The Alerts view (N5): dated, content-free alerts for one job, newest first.

Failures come from run metrics and the dataset's source status (CLI runs included); open
drift from the dataset; waiting candidates from recipe memory; warnings, caps and refused
promotions from Studio's run log. Each alert is a kind and a Rastro code; the page writes
the sentence. A field name is shown only when the job declares it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


def _logged(state: JobState) -> list[dict[str, Any]]:
    declared = {f.name for f in state.job.fields}
    found = []
    for run in state.runs:
        for item in run.sources:
            if run.kind == "promote_refused":
                found.append(
                    _alert(run.at, "promote_refused", item.url, "promotion_refused", "details")
                )
                continue
            if capped(item):
                found.append(_alert(run.at, "cap", item.url, "cap_reached", "raise_cap"))
            for issue in item.issues:
                if issue.code.endswith(CAP_ENDINGS):
                    continue
                name = issue.field if issue.field in declared else None
                found.append(_alert(run.at, "warning", item.url, issue.code, "details", name))
    return found


def alerts(state: JobState) -> list[dict[str, Any]]:
    items = _failures(state) + _logged(state)
    items += [
        _alert(d.observed_at, "drift", url, code(d.verdict), "fix")
        for url, d in state.drift.items()
    ]
    items += [
        _alert(c.at, "quarantine", c.url, "quarantined_candidate", "fix") for c in state.candidates
    ]
    items.sort(key=lambda a: a["at"], reverse=True)
    return [{**a, "at": a["at"].isoformat()} for a in items[:MAX_ALERTS]]
