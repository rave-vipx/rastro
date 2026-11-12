"""New scraper wizard: pick a saved page, give example values, suggest, create a job.

Calls ``suggest`` and the ``rastro new`` job writer; nothing is fetched (INT-002, JOB-001).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.job_loader import _read_local_source
from rastro.application.job_new import new_job
from rastro.application.suggest import suggest
from rastro.domain.job_fields import StringField
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.suggest import MAX_EXAMPLES, Suggestion
from rastro.studio import files
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


def _payload(found: Suggestion[Any]) -> dict[str, Any]:
    rows = [record.data.model_dump(mode="json") for record in found.preview.records]
    return {
        "ok": True,
        "data_trust": "untrusted_source_data",
        "recipe": found.recipe.model_dump(mode="json"),
        "status": str(found.preview.status),
        "records": len(rows),
        "confidence": {k: v.model_dump(mode="json") for k, v in found.confidence.items()},
        "preview": [{k: _cut(v) for k, v in row.items()} for row in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
    }


def routes(studio: Studio) -> list[Route]:
    def page_path(data: dict[str, Any]) -> Any:
        return files.resolve_in_root(studio.root, text(data, "page"), files.PAGE_SUFFIXES)

    async def pages(request: Request) -> Response:
        found, truncated = files.list_pages(studio.root)
        return reply({"files": found, "truncated": truncated})

    async def suggested(request: Request) -> Response:
        data = await body(request)
        path, examples = page_path(data), _examples(data)
        fields = _fields(examples)
        url = optional_text(data, "url")
        records_min = integer(data, "records_min", 1, 1000, default=2)

        def run() -> Suggestion[Any]:
            html = _read_local_source(path, Limits().max_html_bytes, "html")
            return suggest(html, fields, examples=examples, url=url, records_min=records_min)

        return reply(_payload(await run_in_threadpool(run)))

    async def create(request: Request) -> Response:
        data = await body(request)
        target = files.new_job_path(studio.root, text(data, "file"))
        identity = data.get("identity", [])
        if not isinstance(identity, list) or not all(isinstance(i, str) for i in identity):
            raise Refused(400, "invalid_identity")
        dataset = optional_text(data, "dataset")
        if dataset is not None:
            # Job paths are relative to the job file: the dataset lives in the job's
            # folder or below it, in a folder that already exists.
            wanted = (target.parent / dataset).resolve()
            if not wanted.is_relative_to(target.parent.resolve()):
                raise Refused(400, "dataset_outside_job_folder")
            if not wanted.parent.is_dir():
                raise Refused(400, "dataset_folder_missing")
        written = await run_in_threadpool(
            lambda: new_job(
                target,
                page=page_path(data),
                examples=_examples(data),
                url=optional_text(data, "url"),
                records_min=integer(data, "records_min", 1, 1000, default=2),
                dataset=dataset,
                identity=tuple(identity),
            )
        )
        rel = written.relative_to(studio.root).as_posix()
        sha = files.digest(files.read_job_bytes(written))
        return reply({"ok": True, "file": rel, "sha256": sha})

    return [
        studio.route("/api/pages", pages),
        studio.route("/api/suggest", suggested, post=True),
        studio.route("/api/create", create, post=True),
    ]
