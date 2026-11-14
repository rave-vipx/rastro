"""Run endpoints: extract without writing, and load approved records into the dataset.

Extract runs the job against a read-only view of ``recipe_memory`` and writes no file;
load is ``load_job_file`` (DATA-001, REC-006, P-008).
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.active_recipes import active_job
from rastro.application.job_dataset import load_job_file
from rastro.application.jobs import run_job
from rastro.application.preview_recipes import read_only_job
from rastro.domain.job_results import JobRunResult
from rastro.studio.web import Studio, body, flag, plain, reply, text

MAX_ISSUES = 50


def _extract_summary(report: JobRunResult[Any], selections: list[dict[str, Any]]) -> dict[str, Any]:
    by_index = {s["source_index"]: s for s in selections}
    sources = []
    for index, result in enumerate(report.results, start=1):
        chosen = by_index.get(index, {})
        sources.append(
            {
                "source_index": index,
                "source_url": result.source_url,
                "status": str(result.status),
                "records": len(result.records),
                "issues": [plain(issue) for issue in result.issues[:MAX_ISSUES]],
                "recipe_origin": chosen.get("recipe_origin"),
                "recipe_version": chosen.get("recipe_version"),
            }
        )
    return {"ok": True, "job": plain(report.job), "sources": sources}


def routes(studio: Studio) -> list[Route]:
    async def extract(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))

        def run() -> dict[str, Any]:
            _, selections = active_job(job, base_dir=path.parent, allow_absolute_paths=False)
            view = read_only_job(job, base_dir=path.parent)
            report = run_job(view, base_dir=path.parent, **studio.trust)
            return _extract_summary(report, [plain(s) for s in selections])

        return reply(await run_in_threadpool(run))

    async def load(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        drift = flag(data, "drift")
        loaded = await run_in_threadpool(lambda: load_job_file(path, drift=drift, **studio.trust))
        return reply({"ok": True, "result": plain(loaded)})

    return [
        studio.route("/api/extract", extract, post=True),
        studio.route("/api/load", load, post=True),
    ]
