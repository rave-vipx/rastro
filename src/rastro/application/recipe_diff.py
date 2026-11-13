"""Selector-level diff of the comparisons in a repair report (REPAIR-005, P-008, AGENT-002).

Shared by Studio and the MCP tools: old and new selector per field and the record selector,
read from the job's recipe store. Selectors are recipe definitions, never source values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.active_recipes import source_key
from rastro.application.job_loader import resolve_job_path
from rastro.domain.jobs import Job
from rastro.domain.models import Recipe


def _shown(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def recipe_diff(old: Recipe | None, new: Recipe | None) -> dict[str, Any]:
    """Old and new selector per field, and the record selector, for one comparison."""
    before = old.fields if old else {}
    after = new.fields if new else {}
    names = list(dict.fromkeys([*before, *after]))
    return {
        "record_selector": [
            getattr(old, "record_selector", None),
            getattr(new, "record_selector", None),
        ],
        "fields": [
            {
                "field": name,
                "old": _shown(before.get(name)),
                "new": _shown(after.get(name)),
                "changed": before.get(name) != after.get(name),
            }
            for name in names
        ],
    }


def with_diffs(job: Job, base: Path, index: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Add a ``diff`` to every comparison of a JSON repair report for source ``index``."""
    if job.recipe_memory is None:
        return payload
    memory = resolve_job_path(job.recipe_memory, base_dir=base)
    if not memory.is_file():
        return payload
    repository = SQLiteRecipeRepository(memory, read_only=True)
    key = source_key(job, job.sources[index - 1])

    def recipe(version: int) -> Recipe | None:
        stored = repository.get(key, version=version)
        return stored.recipe if stored else None

    for source in payload["sources"]:
        for comparison in source["comparisons"]:
            comparison["diff"] = recipe_diff(
                recipe(comparison["previous_version"]), recipe(comparison["candidate_version"])
            )
    return payload
