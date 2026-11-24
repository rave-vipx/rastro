"""Read-only endpoints for the Projects, Sources and Alerts views.

``GET /api/projects`` summarises every job under the root; ``POST /api/sources`` and
``POST /api/alerts`` take a root-relative ``file``. Responses carry statuses, counts, dates
and Rastro codes; the page writes every sentence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.job_loader import load_job, resolve_job_path
from rastro.studio import files
from rastro.studio.overview_alerts import alerts
from rastro.studio.overview_data import display_name, job_state, project, sources
from rastro.studio.web import HANDLED, Studio, body, reply, text


def _unknown(rel: str) -> dict[str, Any]:
    return {
        "file": rel,
        "name": display_name(Path(rel).stem),
        "dataset": None,
        "health": "unknown",
        "last_run": None,
        "records": 0,
        "sources_total": 0,
        "sources_with_problems": 0,
    }


def _referenced(path: Path, raws: list[str | None]) -> set[Path]:
    found = set()
    for raw in filter(None, raws):
        try:
            found.add(resolve_job_path(raw, base_dir=path.parent).resolve())
        except HANDLED:
            continue
    return found


def projects(root: Path) -> dict[str, Any]:
    names, truncated = files.list_job_files(root)
    rows, used = [], set()
    for rel in names:
        path = root / rel
        try:
            job = load_job(path)
            used |= _referenced(
                path, [job.dataset.path if job.dataset else None, job.recipe_memory]
            )
            rows.append(project(rel, job_state(root, path, job)))
        except HANDLED:
            rows.append(_unknown(rel))  # An invalid job is listed; its errors show in Results.
    databases, more = files.list_databases(root)
    other = [rel for rel in databases if (root / rel).resolve() not in used]
    return {"projects": rows, "other_databases": other, "truncated": truncated or more}


def routes(studio: Studio) -> list[Route]:
    async def listing(_: Request) -> Response:
        return reply(await run_in_threadpool(projects, studio.root))

    async def source_list(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        state = await run_in_threadpool(job_state, studio.root, path, job)
        return reply({"sources": sources(state)})

    async def alert_list(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        state = await run_in_threadpool(job_state, studio.root, path, job)
        return reply({"alerts": alerts(state, state.report)})

    return [
        studio.route("/api/projects", listing),
        studio.route("/api/sources", source_list, post=True),
        studio.route("/api/alerts", alert_list, post=True),
    ]
