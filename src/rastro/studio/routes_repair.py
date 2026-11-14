"""Health and repair: per-source status, propose a repair, promote, write to the job file.

Each action calls the public job API (``repair_job_source``, ``promote_job_recipe``,
``write_serving_recipe``); Studio adds only the selector diff and the digest check
(REPAIR-002-005, REC-006, P-008).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.active_recipes import active_job, source_key
from rastro.application.job_loader import resolve_job_path
from rastro.application.job_repair import promote_job_recipe, repair_job_source
from rastro.application.recipe_apply import write_serving_recipe
from rastro.domain.jobs import FileJobSource, Job
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.models import Recipe
from rastro.domain.recipe_compare import RepairReport
from rastro.studio import files
from rastro.studio.web import Studio, body, flag, integer, plain, reply, text

MAX_SOURCES = 1000
MAX_VERSION = 2_147_483_647


def _existing(raw: str | None, base: Path) -> Path | None:
    if raw is None:
        return None
    path = resolve_job_path(raw, base_dir=base)
    return path if path.is_file() else None


def _statuses(job: Job, base: Path) -> dict[str, dict[str, Any]]:
    file = _existing(job.dataset.path if job.dataset else None, base)
    if file is None or job.dataset is None:
        return {}
    store = SQLiteDatasetStore(file, limits=job.limits)
    if job.dataset.name not in store.dataset_names():
        return {}
    return {s.source_url: plain(s) for s in store.source_status(job.dataset.name)}


def health(job: Job, base: Path) -> dict[str, Any]:
    """Recipe origin and versions, plus stale status from the dataset, for every source."""
    _, selections = active_job(job, base_dir=base, allow_absolute_paths=False)
    memory = _existing(job.recipe_memory, base)
    repository = SQLiteRecipeRepository(memory) if memory is not None else None
    statuses = _statuses(job, base)
    sources = []
    for index, item in enumerate(job.sources, start=1):
        url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
        history = repository.history(source_key(job, item)) if repository else None
        sources.append(
            {
                **plain(selections[index - 1]),
                "source_url": url,
                "versions": [plain(v) for v in history.versions] if history else [],
                "status": statuses.get(url),
            }
        )
    return {"ok": True, "has_memory": job.recipe_memory is not None, "sources": sources}


def _shown(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _diff(old: Recipe | None, new: Recipe | None) -> dict[str, Any]:
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


def _with_diffs(job: Job, base: Path, index: int, report: RepairReport) -> dict[str, Any]:
    payload = plain(report)
    memory = _existing(job.recipe_memory, base)
    if memory is None:
        return payload
    repository = SQLiteRecipeRepository(memory)
    key = source_key(job, job.sources[index - 1])

    def recipe(version: int) -> Recipe | None:
        stored = repository.get(key, version=version)
        return stored.recipe if stored else None

    for source in payload["sources"]:
        for comparison in source["comparisons"]:
            comparison["diff"] = _diff(
                recipe(comparison["previous_version"]), recipe(comparison["candidate_version"])
            )
    return payload


def routes(studio: Studio) -> list[Route]:
    async def checked(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        return reply(await run_in_threadpool(health, job, path.parent))

    async def repair(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)

        def run() -> dict[str, Any]:
            report = repair_job_source(path, index, **studio.trust)
            return {"ok": True, "report": _with_diffs(job, path.parent, index, report)}

        return reply(await run_in_threadpool(run))

    async def promote(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)
        version = integer(data, "version", 1, MAX_VERSION)
        reason = check_reason(text(data, "reason"))
        override = flag(data, "override_gates")

        def run() -> dict[str, Any]:
            report = promote_job_recipe(
                path, index, version, reason=reason, override_gates=override, **studio.trust
            )
            return {"ok": True, "report": _with_diffs(job, path.parent, index, report)}

        return reply(await run_in_threadpool(run))

    async def apply(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)
        studio.check_digest(path, data)
        expected = text(data, "sha256")
        applied = await run_in_threadpool(
            lambda: write_serving_recipe(path, index, expected_digest=expected)
        )
        sha = files.digest(files.read_job_bytes(path))
        return reply(
            {
                "ok": True,
                "changed": applied.changed,
                "recipe_version": applied.recipe_version,
                "sha256": sha,
            }
        )

    return [
        studio.route("/api/health", checked, post=True),
        studio.route("/api/repair", repair, post=True),
        studio.route("/api/promote", promote, post=True),
        studio.route("/api/apply", apply, post=True),
    ]
