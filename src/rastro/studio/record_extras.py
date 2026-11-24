"""Per-record extras for the Results view: field provenance, why a
declared field is empty, instruction-like value flags and whether the record came from an
adapter (``rastro.load_records``) rather than a web extraction.

Values are never altered; flags only label them. Locators are capped like field values.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from rastro.application.changes import changes_since
from rastro.domain.dataset import ChangeKind, StoredRecord
from rastro.domain.injection import value_flags
from rastro.records_api import ADAPTER_LOCATOR
from rastro.studio.run_log import LoggedRun

MAX_LOCATOR = 500


def _provenance(record: StoredRecord) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "source_url": evidence.source_url,
            "observed_at": evidence.observed_at.isoformat(),
            "method": evidence.method,
            "locator": evidence.locator[:MAX_LOCATOR],
            "recipe_version": evidence.recipe_version,
        }
        for name, evidence in record.provenance.items()
    }


def is_adapter(record: StoredRecord) -> bool:
    locators = [evidence.locator for evidence in record.provenance.values()]
    return bool(locators) and all(locator == ADAPTER_LOCATOR for locator in locators)


def extras(record: StoredRecord, declared: list[str], stale_urls: set[str]) -> dict[str, Any]:
    """Keys added to a ``/api/records`` row; existing keys are untouched."""
    failed = record.source_url in stale_urls or any(url in stale_urls for url in record.origins)
    reason = "source_failed" if failed else "not_published"
    return {
        "provenance": _provenance(record),
        "empty": {name: reason for name in declared if record.fields.get(name) is None},
        "flags": value_flags(record.fields),
        "origin": "adapter" if is_adapter(record) else "web",
    }


_CAPPED = {"page_cap", "depth_cap", "request_budget", "time_budget"}
_REFUSED = {"blocked", "robots_denied"}
_HEALTHY = {"complete", "not_modified"}
MAX_LISTED = 50


def _reason(run: LoggedRun, position: int) -> str | None:
    """One plain reason a source is partial: ``page_cap``, ``page_failed`` or ``blocked``."""
    assert run.report is not None
    source = run.report.sources[position]
    reason = source.reason
    if reason in _HEALTHY and source.crawl is not None:
        reason = source.crawl.stop
    if reason in _CAPPED:
        return "page_cap"
    if reason in _REFUSED or source.blocked is not None:
        return "blocked"
    if reason not in _HEALTHY or source.outcome == "partial_success":
        return "page_failed"
    logged = run.sources[position] if position < len(run.sources) else None
    if logged is not None and any(i.code.endswith("_page_failed") for i in logged.issues):
        return "page_failed"
    return None


def why_partial(run: LoggedRun | None) -> list[dict[str, str]]:
    """Per source of the last load: the host, and in plain words why it is not complete.

    Content-free: the run report keeps hosts and paths, never queries or page text.
    """
    if run is None or run.report is None:
        return []
    found = []
    for position, source in enumerate(run.report.sources[:MAX_LISTED]):
        why = _reason(run, position)
        if why is not None:
            found.append({"host": source.host, "path": source.path, "why": why})
    return found


def compare(
    records: tuple[StoredRecord, ...],
    last_success: Mapping[str, datetime],
    runs: tuple[LoggedRun, ...],
) -> tuple[dict[str, Any], dict[str, str]]:
    """What the last load changed, and a ``new``/``removed`` mark per record key.

    Needs two logged loads; with fewer it is the first run and nothing is compared. The store
    keeps no earlier field values, so ``changed`` is the last report's updated count.
    """
    loads = [run for run in runs if run.kind == "load"]
    if len(loads) < 2:
        return {"first_run": True, "new": 0, "removed": 0, "changed": 0}, {}
    since = loads[-2].at
    found = changes_since(records, since, latest_success=last_success)
    keys = {r.key.identity: r.key.model_dump_json() for r in records}
    marks = {
        keys[item.identity]: kind
        for kind, items in (("new", found.new), ("removed", found.removed))
        for item in items
    }
    last = loads[-1].report
    changed = sum(
        (s.stored or {}).get(ChangeKind.UPDATED, 0) for s in (last.sources if last else ())
    )
    summary = {
        "first_run": False,
        "since": since.isoformat(),
        "new": len(found.new),
        "removed": len(found.removed),
        "changed": changed,
    }
    return summary, marks
