"""Dataset browser: paginated records with stale markers, and JSON or HTML export.

Reads the job's ``[dataset]`` file without creating it; values stay labelled untrusted
and every field value is capped at ``MAX_CHARS`` with a ``truncated`` flag (DATA-004, P-008).
The store has no paged read, so a page reads the whole dataset; that read is bounded by the
job's ``Limits`` (``max_records``, ``max_json_bytes``).
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
MAX_CHARS = 2_000


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


def _cap(value: Any) -> tuple[Any, bool]:
    text = value if isinstance(value, str) else None
    if text is None and isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    if text is None or len(text) <= MAX_CHARS:
        return value, False
    return text[:MAX_CHARS] + "…", True


def _capped_fields(fields: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    capped = {name: _cap(value) for name, value in fields.items()}
    return {k: v for k, (v, _) in capped.items()}, any(cut for _, cut in capped.values())


def _capped(record: StoredRecord) -> tuple[StoredRecord, bool]:
    """The record with long values cut in its fields and in every origin's fields."""
    fields, cut = _capped_fields(record.fields)
    origins = {}
    for url, origin in record.origins.items():
        origin_fields, origin_cut = _capped_fields(origin.fields)
        origins[url] = origin.model_copy(update={"fields": origin_fields})
        cut = cut or origin_cut
    return record.model_copy(update={"fields": fields, "origins": origins}), cut


def _capped_identity(key: dict[str, Any]) -> bool:
    """Cut long identity values of a dumped record key in place; True when any was cut."""
    capped = [_cap(value) for value in key["identity"]]
    key["identity"] = [value for value, _ in capped]
    return any(cut for _, cut in capped)


def _dumped(record: StoredRecord) -> tuple[dict[str, Any], bool]:
    """JSON form of a record with every field and identity value capped."""
    capped, cut = _capped(record)
    data: dict[str, Any] = capped.model_dump(mode="json")
    keys = [data["key"], *(origin["key"] for origin in data["origins"].values())]
    cut = any([_capped_identity(key) for key in keys]) or cut
    return data, cut


def _row(record: StoredRecord, stale: set[str]) -> dict[str, Any]:
    data, cut = _dumped(record)
    return {
        "identity": data["key"]["identity"],
        "fields": data["fields"],
        "truncated": cut,
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
    stored = store.records(name)
    if len(stored) > MAX_EXPORT:
        raise ConfigurationError(
            f"More than {MAX_EXPORT} records; use `rastro dataset export` instead"
        )
    capped = [_capped(record) for record in stored]
    records = tuple(record for record, _ in capped)
    if kind == "html":
        stale = {k for s in stale_sources(store, name) for k in s.affected_keys}
        fields = list(dict.fromkeys(field for record in records for field in record.fields))
        html = html_export(records, stale, fields)
        return {"ok": True, "filename": f"{name}.html", "media_type": "text/html", "content": html}
    payload = {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "dataset": name,
        "records": [
            {**data, **({"truncated": True} if cut else {})} for data, cut in map(_dumped, stored)
        ],
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
