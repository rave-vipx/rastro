"""Dataset browser: paginated records with stale markers, and JSON or HTML export.

Reads the job's ``[dataset]`` file without creating it; values stay labelled untrusted
(DATA-004, P-008).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.dataset_load import stale_sources
from rastro.application.job_loader import resolve_job_path
from rastro.cli_dataset_export import html_export
from rastro.domain.dataset import StoredRecord
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.studio.web import Refused, Studio, body, integer, reply, text

MAX_PAGE = 100
MAX_OFFSET = 1_000_000
MAX_EXPORT = 5_000
MAX_CHARS = 500


def _store(studio: Studio, path: Path, job: Job) -> tuple[SQLiteDatasetStore, str]:
    if job.dataset is None:
        raise ConfigurationError("The job has no [dataset] block")
    file = resolve_job_path(job.dataset.path, base_dir=path.parent)
    if not file.is_relative_to(studio.root) or not file.is_file():
        raise ConfigurationError("The dataset does not exist yet; load the job first")
    store = SQLiteDatasetStore(file, limits=job.limits)
    if job.dataset.name not in store.dataset_names():
        raise ConfigurationError("The dataset file has no records for this job yet")
    return store, job.dataset.name


def _stale_keys(store: SQLiteDatasetStore, name: str) -> set[str]:
    return {k.model_dump_json() for s in stale_sources(store, name) for k in s.affected_keys}


def _cut(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CHARS:
        return value[:MAX_CHARS] + "…"
    return value


def _row(record: StoredRecord, stale: set[str]) -> dict[str, Any]:
    return {
        "identity": list(record.key.identity),
        "fields": {k: _cut(v) for k, v in record.model_dump(mode="json")["fields"].items()},
        "last_observed_at": record.last_observed_at.isoformat(),
        "stale": record.key.model_dump_json() in stale,
    }


def _page(store: SQLiteDatasetStore, name: str, offset: int, limit: int) -> dict[str, Any]:
    records = store.records(name)
    stale = _stale_keys(store, name)
    fields = list(dict.fromkeys(field for record in records for field in record.fields))
    return {
        "ok": True,
        "data_trust": "untrusted_source_data",
        "dataset": name,
        "fields": fields,
        "total": len(records),
        "offset": offset,
        "records": [_row(record, stale) for record in records[offset : offset + limit]],
        "stale_sources": len(stale_sources(store, name)),
    }


def _export(store: SQLiteDatasetStore, name: str, kind: str) -> dict[str, Any]:
    records = store.records(name)
    if len(records) > MAX_EXPORT:
        raise ConfigurationError(
            f"More than {MAX_EXPORT} records; use `rastro dataset export` instead"
        )
    if kind == "html":
        stale = {k for s in stale_sources(store, name) for k in s.affected_keys}
        fields = list(dict.fromkeys(field for record in records for field in record.fields))
        html = html_export(records, stale, fields)
        return {"ok": True, "filename": f"{name}.html", "media_type": "text/html", "content": html}
    payload = {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "dataset": name,
        "records": [r.model_dump(mode="json") for r in records],
    }
    content = json.dumps(payload, indent=2, ensure_ascii=True)
    return {
        "ok": True,
        "filename": f"{name}.json",
        "media_type": "application/json",
        "content": content,
    }


def routes(studio: Studio) -> list[Route]:
    async def records(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        offset = integer(data, "offset", 0, MAX_OFFSET, default=0)
        limit = integer(data, "limit", 1, MAX_PAGE, default=25)
        store, name = _store(studio, path, job)
        return reply(await run_in_threadpool(_page, store, name, offset, limit))

    async def export(request: Request) -> Response:
        data = await body(request)
        kind = text(data, "format")
        if kind not in ("json", "html"):
            raise Refused(400, "invalid_format")
        path, _, job = studio.load(text(data, "file"))
        store, name = _store(studio, path, job)
        return reply(await run_in_threadpool(_export, store, name, kind))

    return [
        studio.route("/api/records", records, post=True),
        studio.route("/api/export", export, post=True),
    ]
