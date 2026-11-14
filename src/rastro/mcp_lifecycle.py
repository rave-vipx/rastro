"""MCP job and recipe lifecycle tools: extract, repair, promote, apply, history, drift (AGENT-002,
AGENT-003, REPAIR-002-005, REC-006, DRIFT-001).

Each function wraps the public API used by the CLI with workspace-relative paths, so the same
containment, gates and policies apply. Results are bounded JSON-ready dicts.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.active_recipes import source_key
from rastro.application.dataset_load import stale_sources
from rastro.application.job_loader import load_job
from rastro.application.job_maintenance import current_source_urls
from rastro.application.job_repair import promote_job_recipe, repair_job_source
from rastro.application.jobs import run_job_file
from rastro.application.recipe_apply import write_serving_recipe
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.models import ConfigurationError
from rastro.mcp_tools import _existing, _inside, _plain, bounded, recipe_memory, source_url

_EMPTY: dict[str, list[Any]] = {
    "sources": [],
    "stale_sources": [],
    "open_drift": [],
    "orphaned_sources": [],
}


def job_extract(
    root: Path, path: str, *, allow_egress: bool = False, allow_env: Iterable[str] = ()
) -> dict[str, Any]:
    """Run the job and return the extracted records, as `rastro job run`; no dataset write."""
    report = run_job_file(
        _existing(root, path),
        allow_absolute_paths=False,
        allow_egress=allow_egress,
        allow_env=allow_env,
    )
    return bounded(_plain(report))


def recipe_repair(
    root: Path,
    path: str,
    source: int,
    *,
    min_records: int | None = None,
    min_completeness: float | None = None,
    allow_egress: bool = False,
    allow_env: Iterable[str] = (),
) -> dict[str, Any]:
    """Propose and evaluate repair candidates; promotion follows the job's own ``[repair]``."""
    report = repair_job_source(
        _existing(root, path),
        source,
        min_records=min_records,
        min_completeness=min_completeness,
        allow_absolute_paths=False,
        allow_egress=allow_egress,
        allow_env=allow_env,
    )
    return bounded(_plain(report))


def recipe_promote(
    root: Path,
    path: str,
    source: int,
    version: int,
    *,
    reason: str,
    override_gates: bool = False,
    allow_egress: bool = False,
    allow_env: Iterable[str] = (),
) -> dict[str, Any]:
    """Re-evaluate a stored candidate and activate it only if the gates pass (or are overridden)."""
    try:
        reason = check_reason(reason)
    except ValidationError:
        raise ConfigurationError("reason must be 1 to 500 printable characters") from None
    report = promote_job_recipe(
        _existing(root, path),
        source,
        version,
        reason=reason,
        override_gates=override_gates,
        allow_absolute_paths=False,
        allow_egress=allow_egress,
        allow_env=allow_env,
    )
    return bounded(_plain(report))


def recipe_apply(root: Path, path: str, source: int) -> dict[str, Any]:
    """Write the serving recipe of ``source`` into the job file, as `rastro recipe apply`."""
    applied = write_serving_recipe(_existing(root, path), source, allow_absolute_paths=False)
    return {
        "schema_version": 1,
        "path": path,
        "source_index": applied.source_index,
        "recipe_version": applied.recipe_version,
        "changed": applied.changed,
    }


def recipe_history(root: Path, path: str, source: int) -> dict[str, Any]:
    """Version states and lifecycle events of one source's recipe; creates no store."""
    job, repository = recipe_memory(root, path)
    if not 1 <= source <= len(job.sources):
        raise ConfigurationError("Source index is outside the job sources")
    item = job.sources[source - 1]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source": source,
        "source_url": source_url(item),
        "serving_version": None,
        "versions": [],
        "events": [],
    }
    if repository is None:
        return {**payload, "note": "No recipe memory file yet"}
    history = repository.history(source_key(job, item))
    payload["serving_version"] = history.serving.version if history.serving else None
    payload["versions"] = [v.model_dump(mode="json") for v in history.versions]
    payload["events"] = [e.model_dump(mode="json") for e in history.events]
    return bounded(payload)


def drift_status(root: Path, path: str) -> dict[str, Any]:
    """Freshness, stale sources and open drift of the job's current sources; reads only.

    Dataset sources that are no longer in the job appear only in ``orphaned_sources``.
    """
    job_path = _existing(root, path)
    job = load_job(job_path)
    if job.dataset is None:
        raise ConfigurationError("drift_status requires a [dataset] block in the job file")
    name = job.dataset.name
    payload: dict[str, Any] = {"schema_version": 1, "dataset": name}
    dataset = _inside(job_path.parent, job.dataset.path)
    if not dataset.is_file():
        return {**payload, **_EMPTY, "note": "No dataset yet; run job_load"}
    store = SQLiteDatasetStore(dataset, limits=job.limits)
    if name not in store.dataset_names():
        return {**payload, **_EMPTY, "note": "Dataset not loaded yet"}
    current = set(current_source_urls(job))
    statuses = store.source_status(name)
    payload["sources"] = [s.model_dump(mode="json") for s in statuses if s.source_url in current]
    payload["stale_sources"] = [
        s.model_dump(mode="json") for s in stale_sources(store, name, current)
    ]
    payload["open_drift"] = [
        d.model_dump(mode="json")
        for d in SQLiteDriftMonitor(dataset, limits=job.limits).open_drift(name)
        if d.source_url in current
    ]
    payload["orphaned_sources"] = [s.source_url for s in statuses if s.source_url not in current]
    payload["hint"] = (
        "Structural drift signals are measured by job_load with drift=true; "
        "a stale or failing source is repaired with recipe_repair; open drift is "
        "acknowledged with `rastro job ack-drift` or cleared by promoting a repair"
    )
    return bounded(payload)
