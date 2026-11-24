"""Run endpoints: extract without writing, and load approved records into the dataset.

Extract runs the job against a read-only view of ``recipe_memory`` and writes no file;
load is ``load_job_file``.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from datetime import UTC, datetime
from typing import Any
from weakref import WeakKeyDictionary

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.active_recipes import active_job
from rastro.application.job_dataset import load_job_file
from rastro.application.job_stream import stream_job_async
from rastro.application.jobs import run_job
from rastro.application.preview_recipes import read_only_job
from rastro.cli_errors import classify
from rastro.domain.job_results import JobRunResult
from rastro.studio import run_log
from rastro.studio.operations import alerts, operation
from rastro.studio.run_live import LiveRun, dev_acquisition
from rastro.studio.web import Studio, body, flag, plain, reply, text

MAX_ISSUES = 50
LIVE: WeakKeyDictionary[Studio, LiveRun] = WeakKeyDictionary()


async def _drive(studio: Studio, live: LiveRun, path: Any, job: Any, finish: Any) -> None:
    failure: tuple[Any, ...] = (None, None, None)
    try:
        view = await run_in_threadpool(lambda: read_only_job(job, base_dir=path.parent))
        acquisition = dev_acquisition(view, studio.acquisition, studio.root)
        async for result in stream_job_async(
            view, base_dir=path.parent, acquisition=acquisition, **studio.trust
        ):
            live.add(result)
        live.finish("done")
    except asyncio.CancelledError:
        live.finish("stopped")
    except Exception as error:
        live.finish("failed", classify(error))
        failure = sys.exc_info()
    finally:
        with contextlib.suppress(Exception):  # a failure is already in the snapshot
            finish(*failure)


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
                "pagination_stop": result.pagination_stop,
                "spider": plain(result.spider) if result.spider else None,
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

        with operation(studio, "extract", path) as outcome:
            payload = await run_in_threadpool(run)
            payload["alerts"] = alerts(payload)
            outcome.update(status=payload["job"]["status"], alerts=payload["alerts"])
        return reply(payload)

    async def load(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        drift = flag(data, "drift")
        with operation(studio, "load", path) as outcome:
            loaded = await run_in_threadpool(
                lambda: load_job_file(path, drift=drift, **studio.trust)
            )
            result = plain(loaded)
            run_log.remember(studio.root, path, run_log.from_load(loaded, datetime.now(UTC)))
            messages = alerts(result)
            outcome.update(status=loaded.status, alerts=messages)
        return reply({"ok": True, "result": result, "alerts": messages})

    async def start(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        name = path.relative_to(studio.root).as_posix()
        outcome_cm = operation(studio, "read", path)
        outcome = outcome_cm.__enter__()  # a second concurrent run is refused here (409)
        live = LiveRun(
            name,
            len(job.sources),
            job.spider.time_budget_seconds if job.spider is not None else None,
            studio.clock,
        )

        def finish(*failure: Any) -> None:
            if failure[0] is None:
                outcome.update(status=live.state, alerts=[])
            outcome_cm.__exit__(*failure)

        live.task = asyncio.create_task(_drive(studio, live, path, job, finish))
        LIVE[studio] = live
        return reply(live.snapshot())

    async def status(request: Request) -> Response:
        await body(request)
        live = LIVE.get(studio)
        return reply(live.snapshot() if live else {"ok": True, "state": "idle"})

    async def stop(request: Request) -> Response:
        await body(request)
        live = LIVE.get(studio)
        if live and live.task and live.state == "running":
            live.task.cancel()
            await asyncio.gather(live.task, return_exceptions=True)
        return reply(live.snapshot() if live else {"ok": True, "state": "idle"})

    return [
        studio.route("/api/run/start", start, post=True),
        studio.route("/api/run/status", status, post=True),
        studio.route("/api/run/stop", stop, post=True),
        studio.route("/api/extract", extract, post=True),
        studio.route("/api/load", load, post=True),
    ]
