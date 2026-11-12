"""Workspace-rooted tool operations behind the MCP server (AGENT-002, AGENT-003, P-008).

Each function wraps an existing service used by the CLI, so the same containment, egress and
robots policy applies. Paths are workspace-relative; absolute paths, ``..`` escapes and symlinks
are rejected. Results are JSON-ready dicts. Source values stay labelled untrusted data.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.active_recipes import source_key
from rastro.application.dataset_load import stale_sources
from rastro.application.job_dataset import load_job_file
from rastro.application.job_loader import load_job
from rastro.application.job_preview import bounded_preview, preview_job_file
from rastro.application.job_summary import job_validation_summary
from rastro.application.repair_context import safe_path
from rastro.cli_dataset_export import select_records
from rastro.domain.jobs import FileJobSource, Job, JobSource
from rastro.domain.models import ConfigurationError

MAX_RECORDS = 200
DEFAULT_RECORDS = 50


def _inside(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigurationError("A workspace-relative path is required")
    if any(p.is_absolute() or p.drive for p in (PurePosixPath(raw), PureWindowsPath(raw))):
        raise ConfigurationError("Absolute paths are not allowed; use a workspace-relative path")
    return safe_path(raw, root, False)


def _existing(root: Path, raw: str) -> Path:
    path = _inside(root, raw)
    if not path.is_file():
        raise ConfigurationError("File does not exist in the workspace")
    return path


def _plain(model: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(model.model_dump_json())
    return payload


def bounded(
    payload: dict[str, Any], *, max_items: int = 50, max_chars: int = 500
) -> dict[str, Any]:
    """Cap every list at ``max_items`` and every string at ``max_chars``; say so in
    ``truncated`` (AGENT-002). Applied to tool outputs that can grow with the source."""
    cut = False

    def walk(value: Any) -> Any:
        nonlocal cut
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            cut = cut or len(value) > max_items
            return [walk(item) for item in value[:max_items]]
        if isinstance(value, str) and len(value) > max_chars:
            cut = True
            return value[:max_chars] + "…"
        return value

    result: dict[str, Any] = walk(payload)
    result["truncated"] = bool(payload.get("truncated")) or cut
    return result


def job_validate(root: Path, path: str) -> dict[str, Any]:
    return job_validation_summary(load_job(_existing(root, path)))


def job_preview(root: Path, path: str) -> dict[str, Any]:
    return bounded_preview(preview_job_file(_existing(root, path)))


def job_load(root: Path, path: str, *, drift: bool = False) -> dict[str, Any]:
    """Run the job and load approved fields into its dataset, as `rastro job load` does."""
    job = _existing(root, path)
    return bounded(_plain(load_job_file(job, drift=drift, allow_absolute_paths=False)))


def dataset_records(
    root: Path,
    path: str,
    *,
    dataset: str | None = None,
    sort_field: str | None = None,
    upcoming: bool = False,
    timezone: str = "UTC",
    limit: int = DEFAULT_RECORDS,
) -> dict[str, Any]:
    """Read stored records; a bounded page, plus the stale-source report for the dataset."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECORDS:
        raise ConfigurationError(f"limit must be between 1 and {MAX_RECORDS}")
    store = SQLiteDatasetStore(_existing(root, path))
    names = store.dataset_names()
    if dataset is None:
        if len(names) != 1:
            raise ConfigurationError("Choose a dataset from a file containing multiple datasets")
        dataset = names[0]
    if dataset not in names:
        raise ConfigurationError("Dataset name does not exist in this file")
    records = select_records(store.records(dataset), sort_field, upcoming, timezone=timezone)
    return {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "dataset": dataset,
        "total": len(records),
        "truncated": len(records) > limit,
        "records": [r.model_dump(mode="json") for r in records[:limit]],
        "stale_sources": [s.model_dump(mode="json") for s in stale_sources(store, dataset)],
    }


def source_url(item: JobSource) -> str:
    return item.resolved_source_url() if isinstance(item, FileJobSource) else item.url


def recipe_memory(root: Path, path: str) -> tuple[Job, SQLiteRecipeRepository | None]:
    """The job and its recipe store; ``None`` when the store does not exist yet (never created)."""
    job_path = _existing(root, path)
    job = load_job(job_path)
    if job.recipe_memory is None:
        raise ConfigurationError("The job has no recipe_memory")
    memory = _inside(job_path.parent, job.recipe_memory)
    return job, SQLiteRecipeRepository(memory) if memory.is_file() else None


def recipe_list(root: Path, path: str) -> dict[str, Any]:
    """List, per job source, the stored recipe versions and their lifecycle states."""
    job, repository = recipe_memory(root, path)
    if repository is None:
        return {"schema_version": 1, "sources": [], "note": "No recipe memory file yet"}
    sources = []
    for number, item in enumerate(job.sources, start=1):
        history = repository.history(source_key(job, item))
        sources.append(
            {
                "source": number,
                "source_url": source_url(item),
                "versions": [v.model_dump(mode="json") for v in history.versions],
                "serving_version": history.serving.version if history.serving else None,
            }
        )
    return bounded({"schema_version": 1, "sources": sources})
