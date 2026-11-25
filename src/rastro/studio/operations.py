"""Plain-language alerts and one observed operation snapshot for the Studio page.

There is no engine progress port yet, so the snapshot records only what Studio observes:
which operation started for which job file, and how it ended. It never invents a page or
scroll ordinal, and it carries no page body, URL query or secret.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rastro.studio.web import Refused, Studio

CLEAN = frozenset({"success", "unchanged"})
CAP_STOPS = frozenset({"max_pages", "budget", "record_limit"})
CAP_CODE_ENDINGS = ("_cap", "max_pages", "page_limit", "depth_limit", "budget")
MAX_ALERTS = 50


def _source_alerts(index: int, source: dict[str, Any]) -> list[str]:
    messages = []
    status = source.get("status")
    if isinstance(status, str) and status not in CLEAN:
        messages.append(
            f"Source {index} did not finish cleanly ({status}). "
            "Previously accepted records are kept."
        )
    if isinstance(status, dict) and status.get("stale"):
        messages.append(f"Source {index} is stale: the latest attempt did not refresh its records.")
    if source.get("open_drift"):
        messages.append(f"Source {index} has open drift: review the records or fix this site.")
    if any(v.get("state") == "quarantined" for v in source.get("versions", ())):
        messages.append(f"Source {index} has a quarantined recipe. It is not serving.")
    for comparison in source.get("comparisons", ()):
        decision = (comparison.get("evaluation") or {}).get("decision")
        if decision == "reject":
            messages.append("Promotion refused: this candidate failed the checks and cannot serve.")
        elif decision == "quarantine" and not source.get("promoted_version"):
            messages.append(
                "Candidate quarantined: the checks did not allow promotion. Review it and "
                "promote with a reason, or leave the current recipe serving."
            )
    codes = [str(issue.get("code", "")) for issue in source.get("issues") or ()]
    if source.get("pagination_stop") in CAP_STOPS or any(
        code.endswith(CAP_CODE_ENDINGS) for code in codes
    ):
        messages.append(
            "A collection cap was reached. Accepted records are kept; this run is incomplete."
        )
    return messages


def alerts(payload: dict[str, Any]) -> list[str]:
    """Readable sentences for failure, drift, staleness, quarantine, refusal and caps."""
    messages = []
    if payload.get("status") in {"failed", "storage_error"}:
        messages.append("The run failed. Check the source outcomes before using these results.")
    if payload.get("open_drift"):
        messages.append("The site changed. Review the records, fix this site or acknowledge it.")
    if payload.get("stale_sources"):
        messages.append("Some sources are stale: their last successful records are being kept.")
    for index, source in enumerate(payload.get("sources", ()), 1):
        if isinstance(source, dict):
            messages.extend(_source_alerts(source.get("source_index", index), source))
    return list(dict.fromkeys(messages))[:MAX_ALERTS]


@contextmanager
def operation(studio: Studio, kind: str, path: Path) -> Iterator[dict[str, Any]]:
    """Record the start and the real outcome of one run; refuse a second concurrent run.

    The caller fills the yielded dict with ``status`` and ``alerts``. The alerts are kept
    for that job file so Health repeats them until the next run of the same file.
    """
    if studio.progress.get("state") == "running":
        raise Refused(409, "operation_in_progress")
    name = path.relative_to(studio.root).as_posix()
    studio.progress = {
        "state": "running",
        "operation": kind,
        "file": name,
        "outcome": None,
        "detail": f"Running {kind} for {name}. Source, page and scroll steps are not reported yet.",
    }
    result: dict[str, Any] = {}
    try:
        yield result
    except BaseException:
        studio.progress = {"state": "failed", "operation": kind, "file": name, "outcome": "failed"}
        studio.attention = (path, [f"The last {kind} stopped with an error before it finished."])
        raise
    messages = list(result.get("alerts", ()))
    studio.progress = {
        "state": "completed",
        "operation": kind,
        "file": name,
        "outcome": str(result.get("status", "completed")),
        "alerts": messages,
    }
    studio.attention = (path, messages)


def remembered(studio: Studio, path: Path) -> list[str]:
    """Alerts of the last run of ``path`` in this Studio process, if any."""
    if studio.attention is None or studio.attention[0] != path:
        return []
    return list(studio.attention[1])
