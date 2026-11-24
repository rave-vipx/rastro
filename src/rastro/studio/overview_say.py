"""One plain-language hint and a traffic light per site, read from the stored run report.

The hint is a key plus small facts (a refusal kind, the ladder level that worked); the page
writes the sentence. The light never gets greener than the dataset status says.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from rastro.domain.run_report import RunReport, SourceReport

LIGHT = {
    "ok": "green",
    "warning": "amber",
    "capped": "amber",
    "stale": "red",
    "failed": "red",
    "never": "grey",
}
RANK = {"grey": 0, "green": 1, "amber": 2, "red": 3}
HINT_LIGHT = {
    "robots": "red",
    "blocked": "red",
    "blocked_passed": "amber",
    "repair_waiting": "amber",
    "drift": "amber",
    "repaired": "amber",
}


def _entry(report: RunReport | None, url: str) -> SourceReport | None:
    if report is None:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    return next((s for s in report.sources if s.host == host and s.path == path), None)


def _rung(item: SourceReport) -> str | None:
    ladder = item.acquisition.ladder if item.acquisition else ()
    return next((s.level for s in ladder if s.outcome == "ok" and s.level != "http"), None)


def hint(report: RunReport | None, url: str) -> dict[str, Any] | None:
    """The one thing worth saying about this site, or None when nothing stands out."""
    item = _entry(report, url)
    if item is None:
        return None
    blocked = item.blocked
    if item.reason == "robots_denied" or (blocked is not None and blocked.kind == "robots"):
        return {"key": "robots"}
    if blocked is not None or item.reason == "blocked":
        kind = blocked.kind if blocked else "unknown"
        rung = _rung(item)
        if item.outcome in ("success", "partial_success") and rung:
            return {"key": "blocked_passed", "kind": kind, "rung": rung}
        return {"key": "blocked", "kind": kind}
    repair = item.repair
    if repair is not None:
        if repair.decision == "promoted":
            return {"key": "repaired"}
        if repair.decision == "quarantined":
            return {"key": "repair_waiting"}
        if repair.drift in ("elevated", "high"):
            return {"key": "drift"}
    return None


def light(status: str, say: dict[str, Any] | None) -> str:
    base = LIGHT.get(status, "grey")
    if say is None:
        return base
    return max(base, HINT_LIGHT.get(say["key"], base), key=RANK.__getitem__)
