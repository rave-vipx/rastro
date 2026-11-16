"""N4/N5/N12: next-link pagination, the job's auto-promote switch, the session stealth
grant and the observed operation snapshot.

Auto-promote is job policy and is written to the operator's job file. Stealth is a grant
of this Studio process only and is never written anywhere (ADR 0034).
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.operator_trust import current_trust
from rastro.domain.recipes import Pagination
from rastro.studio.settings import save_auto_promote, save_pagination
from rastro.studio.web import Refused, Studio, body, flag, integer, reply, text

MAX_SOURCE = 999
MAX_PAGES = 50  # The Pagination contract's own cap (ADR 0026).


def routes(studio: Studio) -> list[Route]:
    def granted() -> dict[str, bool]:
        # A grant from the command line or RASTRO_ALLOW_STEALTH stays on for the process.
        from_process = current_trust().allow_stealth
        return {
            "allow_stealth": bool(studio.trust["allow_stealth"]) or from_process,
            "locked_by_operator": from_process,
        }

    async def session(request: Request) -> Response:
        return reply(granted())

    async def grant(request: Request) -> Response:
        data = await body(request)
        if "allow_stealth" not in data:
            raise Refused(400, "missing_allow_stealth")
        studio.trust["allow_stealth"] = flag(data, "allow_stealth")
        return reply(granted())

    async def pagination(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        studio.check_digest(path, data)
        raw_source = data.get("source")
        source = None if raw_source is None else integer(data, "source", 0, MAX_SOURCE)
        wanted = Pagination(
            next=text(data, "next").strip(), max_pages=integer(data, "max_pages", 1, MAX_PAGES)
        )
        sha = await run_in_threadpool(save_pagination, path, text(data, "sha256"), source, wanted)
        return reply({"ok": True, "sha256": sha, "pagination": wanted.model_dump()})

    async def auto_promote(request: Request) -> Response:
        data = await body(request)
        if "enabled" not in data:
            raise Refused(400, "missing_enabled")
        enabled = flag(data, "enabled")
        path = studio.job_path(text(data, "file"))
        studio.check_digest(path, data)
        sha = await run_in_threadpool(save_auto_promote, path, text(data, "sha256"), enabled)
        return reply({"ok": True, "sha256": sha, "auto_promote": enabled})

    async def progress(request: Request) -> Response:
        return reply(dict(studio.progress))

    return [
        studio.route("/api/session", session),
        studio.route("/api/session", grant, post=True),
        studio.route("/api/pagination", pagination, post=True),
        studio.route("/api/auto-promote", auto_promote, post=True),
        studio.route("/api/progress", progress),
    ]
