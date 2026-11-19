"""The Studio development cache skips repeat fetches and is never part of a job run."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from rastro import Job, Source
from rastro.application.dev_cache import CachingAcquisition, DevCache
from rastro.application.jobs import run_job_async
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.results import Outcome

HTML = '<main><article class="c"><h2>One</h2></article></main>'


class CountingAcquisition:
    def __init__(self, body: str | None = HTML) -> None:
        self.calls = 0
        self.body = body

    async def acquire(self, source, *, max_bytes, representation):
        self.calls += 1
        if self.body is None:
            return AcquisitionResult(
                url=source.url,
                body=None,
                status=Outcome.TRANSIENT_FAILURE,
                issue_code="timeout",
            )
        return AcquisitionResult(
            url=source.url,
            body=self.body,
            status=Outcome.SUCCESS,
            issue_code=None,
            http_status=200,
            requests=1,
        )


@pytest.mark.asyncio
async def test_dev_cache_hit_skips_fetch(tmp_path: Path) -> None:
    inner = CountingAcquisition()
    cached = CachingAcquisition(inner, DevCache(tmp_path / "cache"))
    first = await cached.acquire(
        Source(url="https://a.example/p?b=2&a=1"), max_bytes=1000, representation="html"
    )
    # The same canonical URL (query order, tracking keys) is a hit.
    second = await cached.acquire(
        Source(url="https://a.example/p?a=1&utm_source=x&b=2"),
        max_bytes=1000,
        representation="html",
    )
    assert inner.calls == 1
    assert second.body == first.body == HTML
    assert second.requests == 0


@pytest.mark.asyncio
async def test_dev_cache_expires_and_skips_failures(tmp_path: Path) -> None:
    now = [1000.0]
    cache = DevCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: now[0])
    inner = CountingAcquisition()
    cached = CachingAcquisition(inner, cache)
    source = Source(url="https://a.example/p")
    await cached.acquire(source, max_bytes=1000, representation="html")
    now[0] += 61
    await cached.acquire(source, max_bytes=1000, representation="html")
    assert inner.calls == 2
    failing = CountingAcquisition(body=None)
    miss = CachingAcquisition(failing, DevCache(tmp_path / "other"))
    await miss.acquire(source, max_bytes=1000, representation="html")
    await miss.acquire(source, max_bytes=1000, representation="html")
    assert failing.calls == 2


@pytest.mark.asyncio
async def test_dev_cache_keys_by_representation(tmp_path: Path) -> None:
    inner = CountingAcquisition()
    cached = CachingAcquisition(inner, DevCache(tmp_path / "cache"))
    source = Source(url="https://a.example/p")
    await cached.acquire(source, max_bytes=1000, representation="html")
    await cached.acquire(source, max_bytes=1000, representation="json")
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_dev_cache_entries_hold_no_query_secrets(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    cached = CachingAcquisition(CountingAcquisition(), DevCache(directory))
    await cached.acquire(
        Source(url="https://a.example/p?token=hunter2"), max_bytes=1000, representation="html"
    )
    assert not directory.exists() or not any(directory.iterdir())
    await cached.acquire(
        Source(url="https://a.example/p?page=2"), max_bytes=1000, representation="html"
    )
    entry = json.loads(next(directory.iterdir()).read_text())
    assert entry["body"] == HTML and entry["url"] == "https://a.example/p?page=2"


@pytest.mark.asyncio
async def test_dev_cache_never_used_by_load(tmp_path: Path) -> None:
    """A run ignores a populated cache folder, and no job module imports the cache."""
    cache_dir = tmp_path / "cache"
    DevCache(cache_dir).put(
        "https://a.example/p",
        "html",
        AcquisitionResult(
            url="https://a.example/p", body="<p>stale</p>", status=Outcome.SUCCESS, issue_code=None
        ),
    )
    job = Job.model_validate(
        {
            "format_version": 1,
            "sources": ["https://a.example/p"],
            "acquisition": {"allowed_hosts": ["a.example"]},
            "recipe": {
                "strategy": "css",
                "record_selector": "article",
                "fields": {"title": "h2::text"},
            },
            "fields": [{"name": "title", "type": "string"}],
        }
    )
    spy = CountingAcquisition()
    result = await run_job_async(job, base_dir=tmp_path, acquisition=spy)
    assert spy.calls == 1
    assert result.results[0].records
    src = Path(__file__).parents[2] / "src" / "rastro"
    importers = [
        path.name
        for path in src.rglob("*.py")
        if path.name != "dev_cache.py"
        and any(
            (isinstance(n, ast.ImportFrom) and (n.module or "").endswith("dev_cache"))
            or (isinstance(n, ast.Import) and any(a.name.endswith("dev_cache") for a in n.names))
            for n in ast.walk(ast.parse(path.read_text()))
        )
    ]
    forbidden = {
        "jobs.py",
        "job_loader.py",
        "job_dataset.py",
        "job_execution.py",
        "job_stream.py",
        "cli.py",
    }
    assert not forbidden & set(importers)
