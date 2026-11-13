"""Job file endpoints: page, listing, validate, preview, correct a selector, connect (P-008)."""

from __future__ import annotations

import posixpath
import shlex
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from rastro.application.job_loader import load_job, validate_job_paths
from rastro.application.job_preview import bounded_preview, preview_job
from rastro.domain.jobs import Job
from rastro.studio import editing, files
from rastro.studio.web import HEADERS, Refused, Studio, body, reply, text

STATIC = Path(__file__).parent / "static"
ASSETS = frozenset({"studio.js", "studio.css", "wizard.js", "operate.js", "dom.js"})


def _edit_of(data: dict[str, Any]) -> editing.SelectorEdit | None:
    raw = data.get("edit")
    if raw is None:
        return None
    source = raw.get("source") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or isinstance(source, bool):
        raise Refused(400, "invalid_edit")
    if not (source is None or isinstance(source, int)):
        raise Refused(400, "invalid_edit")
    field, selector = raw.get("field"), raw.get("selector")
    if not isinstance(field, str) or not isinstance(selector, str):
        raise Refused(400, "invalid_edit")
    return editing.SelectorEdit(field=field, selector=selector, source=source)


def _edited(studio: Studio, data: dict[str, Any]) -> tuple[Path, bytes, Job, bytes]:
    """Return (path, original bytes, job to use, bytes to write); validates all of it."""
    path = studio.job_path(text(data, "file"))
    original = files.read_job_bytes(path)
    edit = _edit_of(data)
    if edit is None:
        return path, original, load_job(path), original
    new_text, job = editing.edited_job(original.decode("utf-8"), path.suffix, edit)
    validate_job_paths(job, base_dir=path.parent)
    return path, original, job, new_text.encode("utf-8")


def routes(studio: Studio) -> list[Route]:
    async def page(request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers=HEADERS)

    async def asset(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in ASSETS:
            return reply({"error": "not_found"}, 404)
        return FileResponse(STATIC / name, headers=HEADERS)

    async def jobs(request: Request) -> Response:
        found, truncated = files.list_job_files(studio.root)
        return reply({"root": studio.root.name, "files": found, "truncated": truncated})

    async def validate(request: Request) -> Response:
        data = await body(request)
        _, sha, job = studio.load(text(data, "file"))
        return reply(
            {
                "ok": True,
                "sha256": sha,
                "selectors": editing.list_selectors(job),
                "has_dataset": job.dataset is not None,
                "has_memory": job.recipe_memory is not None,
                "sources": len(job.sources),
            }
        )

    async def preview(request: Request) -> Response:
        data = await body(request)
        path, original, job, _ = _edited(studio, data)
        shown = await run_in_threadpool(preview_job, job, base_dir=path.parent, **studio.trust)
        return reply(
            {"ok": True, "sha256": files.digest(original), "preview": bounded_preview(shown)}
        )

    async def accept(request: Request) -> Response:
        data = await body(request)
        if _edit_of(data) is None:
            raise Refused(400, "invalid_edit")
        path, original, _, raw = _edited(studio, data)
        if files.digest(original) != text(data, "sha256"):
            raise Refused(409, "file_changed")
        files.write_atomic(path, raw, expected_digest=files.digest(original))
        reloaded = load_job(path)
        return reply(
            {"ok": True, "sha256": files.digest(raw), "selectors": editing.list_selectors(reloaded)}
        )

    async def connect(request: Request) -> Response:
        path, _, job = studio.load(request.query_params.get("file", ""))
        return reply(_commands(path.relative_to(studio.root).as_posix(), job))

    return [
        studio.route("/", page),
        studio.route("/static/{name}", asset),
        studio.route("/api/jobs", jobs),
        studio.route("/api/connect", connect),
        studio.route("/api/validate", validate, post=True),
        studio.route("/api/preview", preview, post=True),
        studio.route("/api/accept", accept, post=True),
    ]


def _commands(rel: str, job: Job) -> dict[str, Any]:
    quoted = shlex.quote(rel)
    commands = [
        f"rastro job validate {quoted}",
        f"rastro job preview {quoted}",
        f"rastro job run {quoted} --output json",
    ]
    sdk = f"from rastro import run_job_file\n\nresult = run_job_file({rel!r})"
    if job.dataset is not None:
        dataset = shlex.quote(posixpath.join(posixpath.dirname(rel), job.dataset.path))
        commands += [
            f"rastro job load {quoted}",
            f"rastro dataset export {dataset} --format json",
            f"rastro dataset export {dataset} --format html",
        ]
    return {"cli": commands, "sdk": sdk, "note": "Run from the Studio root directory."}
