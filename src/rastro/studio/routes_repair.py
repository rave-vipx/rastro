"""Health and repair: per-source status, propose a repair, promote, write to the job file.

Each action calls the public job API (``repair_job_source``, ``promote_job_recipe``,
``write_serving_recipe``); Studio adds only the selector diff and the digest check
(REPAIR-002-005, REC-006, P-008).

"Fix this site" uses the Studio model when one is configured (N7). Studio cannot override
failed gates: promotion re-runs them and a refusal is shown as an alert (N5, N7, ADR 0034).
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.active_recipes import active_job, source_key
from rastro.application.job_loader import resolve_job_path
from rastro.application.job_maintenance import current_source_urls
from rastro.application.job_repair import promote_job_recipe, repair_job_source
from rastro.application.recipe_apply import write_serving_recipe
from rastro.application.recipe_diff import with_diffs
from rastro.domain.jobs import Job
from rastro.domain.lifecycle_history import check_reason
from rastro.studio import files
from rastro.studio.operations import alerts, operation, remembered
from rastro.studio.web import Refused, Studio, body, flag, integer, plain, reply, text

MAX_SOURCES = 1000
MAX_VERSION = 2_147_483_647


def _existing(raw: str | None, base: Path) -> Path | None:
    if raw is None:
        return None
    path = resolve_job_path(raw, base_dir=base)
    return path if path.is_file() else None


def _statuses(job: Job, base: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Source status and open drift by source URL (DRIFT-005)."""
    if job.dataset is not None:
        job.dataset.require_sqlite_views()
    file = _existing(job.dataset.path if job.dataset else None, base)
    if file is None or job.dataset is None:
        return {}, {}
    store = SQLiteDatasetStore(file, limits=job.limits)
    if job.dataset.name not in store.dataset_names():
        return {}, {}
    drift = SQLiteDriftMonitor(file, limits=job.limits).open_drift(job.dataset.name)
    return (
        {s.source_url: plain(s) for s in store.source_status(job.dataset.name)},
        {d.source_url: plain(d) for d in drift},
    )


def health(job: Job, base: Path) -> dict[str, Any]:
    """Recipe origin and versions, plus stale status from the dataset, for every source."""
    _, selections = active_job(job, base_dir=base, allow_absolute_paths=False)
    memory = _existing(job.recipe_memory, base)
    repository = SQLiteRecipeRepository(memory) if memory is not None else None
    statuses, drift = _statuses(job, base)
    sources = []
    for index, (item, url) in enumerate(zip(job.sources, current_source_urls(job), strict=True), 1):
        history = repository.history(source_key(job, item)) if repository else None
        sources.append(
            {
                **plain(selections[index - 1]),
                "source_url": url,
                "versions": [plain(v) for v in history.versions] if history else [],
                "status": statuses.get(url),
                "open_drift": drift.get(url),
            }
        )
    payload = {"ok": True, "has_memory": job.recipe_memory is not None, "sources": sources}
    return {**payload, "alerts": alerts(payload)}


NO_JUDGE = (
    "Auto-promote is on, but no model is configured to act as the semantic judge, so this "
    "repair was not promoted. Review the candidate and promote it with a reason, or start "
    "Studio with --model (or give the job a [model])."
)


def has_judge(job: Job, studio_model: object | None) -> bool:
    """Whether repair would get a model to judge meaning, mirroring ``job_repair._assist``.

    The Studio model, the job's own ``[model]``, or ``[repair] model = true`` with the
    operator's ``RASTRO_MODEL``. No other environment model is consulted.
    """
    if studio_model is not None or job.model is not None:
        return True
    wants_env = job.repair is not None and bool(job.repair.model)
    return wants_env and bool(os.environ.get("RASTRO_MODEL", "").strip())


def failure_command(rel: str) -> str:
    """The exact command that runs the operator's hook on failure (the job cannot set it)."""
    hook = shlex.quote("python notify.py")  # The README's copy-paste hook script.
    return f"rastro job load {shlex.quote(rel)} --drift --on-failure {hook}"


def routes(studio: Studio) -> list[Route]:
    async def checked(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        payload = await run_in_threadpool(health, job, path.parent)
        payload["alerts"] = list(dict.fromkeys([*remembered(studio, path), *payload["alerts"]]))
        payload["on_failure_command"] = failure_command(path.relative_to(studio.root).as_posix())
        return reply(payload)

    async def repair(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)

        # N12: Studio's switch promises the semantic judge. Without a model to provide it,
        # this repair stores candidates only; the core API keeps its deterministic default.
        held = bool(job.repair and job.repair.auto_promote) and not has_judge(job, studio.model)

        def run() -> dict[str, Any]:
            report = repair_job_source(
                path,
                index,
                model=studio.model,
                auto_promote=False if held else None,  # None keeps the job's own policy
                **studio.trust,
            )
            payload = with_diffs(job, path.parent, index, plain(report))
            messages = ([NO_JUDGE] if held else []) + alerts(payload)
            return {"ok": True, "report": payload, "alerts": messages}

        with operation(studio, "repair", path) as outcome:
            payload = await run_in_threadpool(run)
            promoted = any(s.get("promoted_version") for s in payload["report"]["sources"])
            status = "promoted" if promoted else "candidates"
            outcome.update(status=status, alerts=payload["alerts"])
        return reply(payload)

    async def promote(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)
        version = integer(data, "version", 1, MAX_VERSION)
        reason = check_reason(text(data, "reason"))
        if flag(data, "override_gates"):  # The CLI keeps that operator choice; Studio does not.
            raise Refused(400, "gate_override_refused")

        def run() -> dict[str, Any]:
            report = promote_job_recipe(path, index, version, reason=reason, **studio.trust)
            payload = with_diffs(job, path.parent, index, plain(report))
            return {"ok": True, "report": payload, "alerts": alerts(payload)}

        with operation(studio, "promote", path) as outcome:
            payload = await run_in_threadpool(run)
            promoted = any(s.get("promoted_version") for s in payload["report"]["sources"])
            outcome.update(status="promoted" if promoted else "refused", alerts=payload["alerts"])
        return reply(payload)

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
