"""Shared HTTP plumbing for Studio routers: guard, bodies, typed inputs, JSON replies (P-008).

Security posture: loopback only, Host and Origin checks, a per-run token on every API call,
paths confined to one root, bounded bodies, a strict Content-Security-Policy.
"""

from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import SchemaError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from rastro.application.atomic_files import FileChangedError
from rastro.application.job_loader import load_job
from rastro.cli_errors import classify
from rastro.domain.jobs import Job
from rastro.domain.models import RastroError
from rastro.studio import files

MAX_BODY = 16 * 1024
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
type Handler = Callable[[Request], Awaitable[Response]]


class Refused(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code


def reply(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=HEADERS)


def plain(model: Any) -> dict[str, Any]:
    """A Pydantic model as JSON-ready data (Decimals, datetimes and enums as JSON)."""
    payload: dict[str, Any] = json.loads(model.model_dump_json())
    return payload


async def body(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise Refused(413, "request_too_large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise Refused(413, "request_too_large")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except ValueError:
        raise Refused(400, "invalid_json") from None
    if not isinstance(data, dict):
        raise Refused(400, "invalid_json")
    return data


def text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise Refused(400, f"missing_{key}")
    return value


def optional_text(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise Refused(400, f"invalid_{key}")
    return value


def integer(data: dict[str, Any], key: str, low: int, high: int, default: int | None = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refused(400, f"invalid_{key}")
    return value


def flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise Refused(400, f"invalid_{key}")
    return value


def _origin_ok(request: Request) -> bool:
    """No Origin (same-origin GET, CLI clients) or exactly this Studio's own origin."""
    origin = request.headers.get("origin")
    return origin is None or origin == f"http://{request.headers.get('host', '')}"


class Studio:
    """The served root and this run's token; routers resolve every path through it."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.token = secrets.token_urlsafe(32)

    def job_path(self, name: str) -> Path:
        return files.resolve_in_root(self.root, name)

    def load(self, name: str) -> tuple[Path, str, Job]:
        """Path, sha256 of the bytes read and the validated job for a root-relative name."""
        path = self.job_path(name)
        raw = files.read_job_bytes(path)
        return path, files.digest(raw), load_job(path)

    def check_digest(self, path: Path, data: dict[str, Any]) -> None:
        """Refuse an edit when the file no longer matches the digest the page last saw."""
        if files.digest(files.read_job_bytes(path)) != text(data, "sha256"):
            raise Refused(409, "file_changed")

    def guard(self, request: Request, *, api: bool) -> None:
        """Exact origin everywhere; the token on every ``/api`` call, GETs included."""
        if not _origin_ok(request):
            raise Refused(403, "origin_refused")
        if api or request.method != "GET":
            sent = request.headers.get("x-studio-token", "")
            if not hmac.compare_digest(sent.encode(), self.token.encode()):
                raise Refused(403, "token_required")

    def route(self, path: str, handler: Handler, *, post: bool = False) -> Route:
        """A route whose handler runs only after the guard; failures become JSON errors."""

        async def endpoint(request: Request) -> Response:
            try:
                self.guard(request, api=path.startswith("/api/"))
                return await handler(request)
            except FileChangedError:
                return reply({"ok": False, "error": "file_changed"}, 409)
            except Refused as refused:
                return reply({"ok": False, "error": refused.code}, refused.status)
            except HANDLED as error:
                return reply({"ok": False, **classify(error)}, 400)

        return Route(path, endpoint, methods=["POST"] if post else ["GET"])
