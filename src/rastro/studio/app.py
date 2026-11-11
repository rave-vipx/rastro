"""Studio web app: a thin Starlette layer over the job services (P-008).

Security posture: loopback only, Host and Origin checks, a per-run token on every POST,
paths confined to one root, bounded bodies, a strict Content-Security-Policy.
"""

from __future__ import annotations

import hmac
import json
import posixpath
import secrets
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import SchemaError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from rastro.application.job_loader import load_job, validate_job_paths
from rastro.application.job_preview import bounded_preview, preview_job
from rastro.cli_errors import classify
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError, RastroError
from rastro.studio import editing, files

MAX_BODY = 16 * 1024
LOCAL_HOSTS = ("127.0.0.1", "localhost")
STATIC = Path(__file__).parent / "static"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
HANDLED = (OSError, ValueError, ValidationError, SchemaError, RastroError)


class _Refused(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=HEADERS)


def _origin_ok(request: Request) -> bool:
    """No Origin (same-origin GET, CLI clients) or exactly this Studio's own origin."""
    origin = request.headers.get("origin")
    return origin is None or origin == f"http://{request.headers.get('host', '')}"


async def _body(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise _Refused(413, "request_too_large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise _Refused(413, "request_too_large")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except ValueError:
        raise _Refused(400, "invalid_json") from None
    if not isinstance(data, dict):
        raise _Refused(400, "invalid_json")
    return data


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _Refused(400, f"missing_{key}")
    return value


def _edit_of(data: dict[str, Any]) -> editing.SelectorEdit | None:
    raw = data.get("edit")
    if raw is None:
        return None
    source = raw.get("source") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or isinstance(source, bool):
        raise _Refused(400, "invalid_edit")
    if not (source is None or isinstance(source, int)):
        raise _Refused(400, "invalid_edit")
    field, selector = raw.get("field"), raw.get("selector")
    if not isinstance(field, str) or not isinstance(selector, str):
        raise _Refused(400, "invalid_edit")
    return editing.SelectorEdit(field=field, selector=selector, source=source)


class Studio:
    """Holds the root and per-run token; endpoint methods are the application services."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.token = secrets.token_urlsafe(32)

    def _load(self, name: str) -> tuple[Path, str, Job]:
        path = files.resolve_in_root(self.root, name)
        raw = files.read_job_bytes(path)
        job = load_job(path)
        return path, files.digest(raw), job

    def _edited(self, data: dict[str, Any]) -> tuple[Path, bytes, Job, bytes]:
        """Return (path, original bytes, job to use, bytes to write); validates all of it."""
        path = files.resolve_in_root(self.root, _text(data, "file"))
        original = files.read_job_bytes(path)
        edit = _edit_of(data)
        if edit is None:
            return path, original, load_job(path), original
        text, job = editing.edited_job(original.decode("utf-8"), path.suffix, edit)
        raw = text.encode("utf-8")
        validate_job_paths(job, base_dir=path.parent)
        return path, original, job, raw

    def _guard(self, request: Request) -> None:
        if not _origin_ok(request):
            raise _Refused(403, "origin_refused")
        if request.method == "POST":
            sent = request.headers.get("x-studio-token", "")
            if not hmac.compare_digest(sent.encode(), self.token.encode()):
                raise _Refused(403, "token_required")

    async def page(self, request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers=HEADERS)

    async def asset(self, request: Request) -> Response:
        name = request.path_params["name"]
        if name not in {"studio.js", "studio.css"}:
            return _json({"error": "not_found"}, 404)
        return FileResponse(STATIC / name, headers=HEADERS)

    async def jobs(self, request: Request) -> Response:
        found, truncated = files.list_job_files(self.root)
        return _json({"root": self.root.name, "files": found, "truncated": truncated})

    async def validate(self, request: Request) -> Response:
        data = await _body(request)
        _, sha, job = self._load(_text(data, "file"))
        return _json(
            {
                "ok": True,
                "sha256": sha,
                "selectors": editing.list_selectors(job),
                "has_dataset": job.dataset is not None,
            }
        )

    async def preview(self, request: Request) -> Response:
        data = await _body(request)
        path, original, job, _ = self._edited(data)
        shown = await run_in_threadpool(preview_job, job, base_dir=path.parent)
        return _json(
            {"ok": True, "sha256": files.digest(original), "preview": bounded_preview(shown)}
        )

    async def accept(self, request: Request) -> Response:
        data = await _body(request)
        if _edit_of(data) is None:
            raise _Refused(400, "invalid_edit")
        path, original, _, raw = self._edited(data)
        if files.digest(original) != _text(data, "sha256"):
            raise _Refused(409, "file_changed")
        files.write_atomic(path, raw)
        reloaded = load_job(path)
        return _json(
            {
                "ok": True,
                "sha256": files.digest(raw),
                "selectors": editing.list_selectors(reloaded),
            }
        )

    async def connect(self, request: Request) -> Response:
        name = request.query_params.get("file", "")
        path, _, job = self._load(name)
        rel = path.relative_to(self.root).as_posix()
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
        return _json({"cli": commands, "sdk": sdk, "note": "Run from the Studio root directory."})


def _guarded(
    studio: Studio, handler: Callable[[Request], Awaitable[Response]]
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        try:
            studio._guard(request)
            return await handler(request)
        except _Refused as refused:
            return _json({"ok": False, "error": refused.code}, refused.status)
        except HANDLED as error:
            return _json({"ok": False, **classify(error)}, 400)

    return endpoint


def create_app(root: str | Path) -> tuple[Starlette, str]:
    """Build the app for ``root``; returns it with this run's token (for tests and the CLI)."""
    folder = Path(root)
    if not folder.is_dir():
        raise ConfigurationError("Studio root must be an existing directory")
    studio = Studio(folder)
    routes = [
        Route("/", _guarded(studio, studio.page)),
        Route("/static/{name}", _guarded(studio, studio.asset)),
        Route("/api/jobs", _guarded(studio, studio.jobs)),
        Route("/api/connect", _guarded(studio, studio.connect)),
        Route("/api/validate", _guarded(studio, studio.validate), methods=["POST"]),
        Route("/api/preview", _guarded(studio, studio.preview), methods=["POST"]),
        Route("/api/accept", _guarded(studio, studio.accept), methods=["POST"]),
    ]
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))]
    return Starlette(routes=routes, middleware=middleware), studio.token
