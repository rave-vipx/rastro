"""Studio "Ask AI": a configured model proposes, Rastro verifies (INT-001, REPAIR-001, P-008).

Active only when Studio was started with a model (``--model`` or ``RASTRO_MODEL``). The
wizard asks for a recipe from field names alone, kept only if it extracts valid records; the
repair view runs ``repair_job_source(..., model=...)``: relocation first, then model
proposals composed with the serving selectors, the same gates, and the semantic judge. A
candidate is promoted only when the job sets ``[repair] auto_promote = true`` and the judge
agrees; otherwise it waits for promotion with a reason. Every reply says whether page
content went to a remote model (``--allow-remote``).
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.job_loader import _read_local_source
from rastro.application.job_repair import repair_job_source
from rastro.application.model_recipes import ModelAnswer, recipe_for_page
from rastro.application.recipe_diff import with_diffs
from rastro.domain.job_fields import build_job_schema
from rastro.domain.models import Limits
from rastro.ports.model import ModelPort
from rastro.studio import files
from rastro.studio.routes_repair import MAX_SOURCES
from rastro.studio.routes_wizard import MAX_ROWS, _cut, _fields
from rastro.studio.web import Refused, Studio, body, integer, optional_text, plain, reply, text

MAX_FIELDS = 30


def _names(data: dict[str, Any]) -> list[str]:
    raw = data.get("fields")
    if (
        not isinstance(raw, list)
        or not 1 <= len(raw) <= MAX_FIELDS
        or not all(isinstance(name, str) and name for name in raw)
    ):
        raise Refused(400, "invalid_fields")
    return list(dict.fromkeys(raw))


def _page_answer(answer: ModelAnswer) -> dict[str, Any]:
    if answer.recipe is None or answer.result is None:
        return {"ok": True, "found": False, "failure": answer.failure, "calls": answer.calls}
    rows = [record.data.model_dump(mode="json") for record in answer.result.records]
    return {
        "ok": True,
        "found": True,
        "calls": answer.calls,
        "data_trust": "untrusted_source_data",
        "recipe": answer.recipe.model_dump(mode="json"),
        "records": len(rows),
        "preview": [{k: _cut(v) for k, v in row.items()} for row in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
    }


def routes(studio: Studio) -> list[Route]:
    def model() -> ModelPort:
        if studio.model is None:
            raise Refused(409, "no_model_configured")
        return studio.model

    async def status(request: Request) -> Response:
        return reply({"enabled": studio.model is not None, "remote": studio.model_remote})

    async def suggest(request: Request) -> Response:
        data = await body(request)
        port = model()
        page = files.resolve_in_root(studio.root, text(data, "page"), files.PAGE_SUFFIXES)
        schema = build_job_schema(_fields([dict.fromkeys(_names(data), "")]))
        html = _read_local_source(page, Limits().max_html_bytes, "html")
        answer = await recipe_for_page(port, html, schema, url=optional_text(data, "url"))
        return reply({**_page_answer(answer), "remote": studio.model_remote})

    async def repair(request: Request) -> Response:
        data = await body(request)
        port = model()
        path, _, job = studio.load(text(data, "file"))
        index = integer(data, "source", 1, MAX_SOURCES)
        report = await run_in_threadpool(lambda: repair_job_source(path, index, model=port))
        payload = with_diffs(job, path.parent, index, plain(report))
        return reply({"ok": True, "report": payload, "remote": studio.model_remote})

    return [
        studio.route("/api/ai", status),
        studio.route("/api/ai/suggest", suggest, post=True),
        studio.route("/api/ai/repair", repair, post=True),
    ]
