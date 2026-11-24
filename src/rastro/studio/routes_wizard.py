"""New scraper wizard: URL (or saved page), example values, suggest, create a job.

URL-first path fetches through the acquisition port and writes a ``kind = "url"`` job.
A saved HTML page remains available for offline replay; it is not required.
Suggest stores the fetched page on the Studio run so create reuses it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.job_from_url import (
    FetchedUrl,
    html_from_url,
    new_job_from_url,
    prepare_url_policy,
)
from rastro.application.job_loader import _read_local_source
from rastro.application.job_new import new_job
from rastro.application.navigation_offer import exploration_offer
from rastro.application.suggest import suggest
from rastro.domain.job_fields import StringField
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.suggest import MAX_EXAMPLES, Suggestion
from rastro.studio import files, routes_pick, wizard_more
from rastro.studio.web import Refused, Studio, body, integer, optional_text, reply, text

MAX_ROWS = 50
MAX_CHARS = 500


def _examples(data: dict[str, Any]) -> list[dict[str, str]]:
    raw = data.get("examples")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_EXAMPLES:
        raise Refused(400, "invalid_examples")
    if not all(
        isinstance(item, dict) and all(isinstance(v, str) for v in item.values()) for item in raw
    ):
        raise Refused(400, "invalid_examples")
    return raw


def _fields(examples: list[dict[str, str]]) -> list[StringField]:
    names = dict.fromkeys(name for example in examples for name in example)
    try:
        return [StringField(name=name) for name in names]
    except ValidationError:
        raise ConfigurationError(
            "Field names must start with a letter and use letters, digits or _"
        ) from None


def _cut(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CHARS:
        return value[:MAX_CHARS] + "…"
    return value


def _payload(
    found: Suggestion[Any], html: str, records_min: int, *, allow_http: bool = False
) -> dict[str, Any]:
    rows = [record.data.model_dump(mode="json") for record in found.preview.records]
    offer = exploration_offer(html, len(rows), records_min)
    return {
        "explore": offer.reason if offer is not None else None,
        "ok": True,
        "data_trust": "untrusted_source_data",
        "recipe": found.recipe.model_dump(mode="json"),
        "status": str(found.preview.status),
        "records": len(rows),
        "confidence": {k: v.model_dump(mode="json") for k, v in found.confidence.items()},
        "preview": [{k: _cut(v) for k, v in row.items()} for row in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
        "allow_http": allow_http,
    }


def routes(studio: Studio) -> list[Route]:
    def page_path(data: dict[str, Any]) -> Any:
        return files.resolve_in_root(studio.root, text(data, "page"), files.PAGE_SUFFIXES)

    async def pages(request: Request) -> Response:
        found, truncated = files.list_pages(studio.root)
        return reply({"files": found, "truncated": truncated})

    async def suggested(request: Request) -> Response:
        data = await body(request)
        examples = _examples(data)
        fields = _fields(examples)
        url = optional_text(data, "url")
        records_min = integer(data, "records_min", 1, 1000, default=2)
        page = optional_text(data, "page")

        def run() -> tuple[Suggestion[Any], bool, str]:
            if page is not None:
                studio.url_preview = None
                html = _read_local_source(page_path(data), Limits().max_html_bytes, "html")
                found = suggest(html, fields, examples=examples, url=url, records_min=records_min)
                return found, False, html
            if url is None:
                raise Refused(400, "missing_url")
            fetched = html_from_url(url, acquisition=studio.acquisition)
            studio.url_preview = (url, studio.clock(), fetched)
            return (
                suggest(
                    fetched.html,
                    fields,
                    examples=examples,
                    url=fetched.final_url,
                    records_min=records_min,
                ),
                fetched.allow_http,
                fetched.html,
            )

        found, allow_http, html = await run_in_threadpool(run)
        return reply(_payload(found, html, records_min, allow_http=allow_http))

    async def create(request: Request) -> Response:
        data = await body(request)
        target = files.new_job_path(studio.root, text(data, "file"))
        identity = data.get("identity", [])
        if not isinstance(identity, list) or not all(isinstance(i, str) for i in identity):
            raise Refused(400, "invalid_identity")
        dataset = _beside(target, optional_text(data, "dataset"), "dataset")
        memory = _beside(target, optional_text(data, "recipe_memory"), "recipe_memory")
        examples = _examples(data)
        url = optional_text(data, "url")
        page = optional_text(data, "page")
        records_min = integer(data, "records_min", 1, 1000, default=2)

        def write() -> tuple[Path, bool]:
            if page is not None:
                studio.url_preview = None
                written = new_job(
                    target,
                    page=page_path(data),
                    examples=examples,
                    url=url,
                    records_min=records_min,
                    dataset=dataset,
                    identity=tuple(identity),
                    recipe_memory=memory,
                )
                return written, False
            if url is None:
                raise Refused(400, "missing_url")
            written, allow_http = new_job_from_url(
                target,
                url=url,
                examples=examples,
                records_min=records_min,
                dataset=dataset,
                identity=tuple(identity),
                recipe_memory=memory,
                acquisition=studio.acquisition,
                fetched=_preview(studio, url),
            )
            studio.url_preview = None
            return written, allow_http

        written, allow_http = await run_in_threadpool(write)
        rel = written.relative_to(studio.root).as_posix()
        sha = files.digest(files.read_job_bytes(written))
        return reply({"ok": True, "file": rel, "sha256": sha, "allow_http": allow_http})

    return [
        studio.route("/api/pages", pages),
        studio.route("/api/suggest", suggested, post=True),
        studio.route("/api/create", create, post=True),
        *routes_pick.routes(studio),
        *wizard_more.routes(studio),
    ]


def _beside(target: Path, name: str | None, key: str) -> str | None:
    """Job paths are relative to the job file: the file lives in the job's folder or below
    it, in a folder that already exists."""
    if name is None:
        return None
    wanted = (target.parent / name).resolve()
    if not wanted.is_relative_to(target.parent.resolve()):
        raise Refused(400, f"{key}_outside_job_folder")
    if not wanted.parent.is_dir():
        raise Refused(400, f"{key}_folder_missing")
    return name


def _preview(studio: Studio, url: str) -> FetchedUrl | None:
    cached = studio.url_preview
    studio.url_preview = None
    if cached is not None and cached[0] == url and 0 <= studio.clock() - cached[1] < 600:
        _, policy = prepare_url_policy(url)
        if cached[2].policy == policy:
            return cached[2]
    return None
