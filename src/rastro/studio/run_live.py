"""A read that runs in the background so Studio can show it while it goes on.

``LiveRun`` collects what ``stream_job_async`` yields: pages read, records so far, a few sample
rows and, when an unbounded crawl stopped on its time budget, that the next run continues from
the saved place. Sample values are plain text for the page to show as text; nothing here is
written to disk except the short-lived page cache.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rastro.application.dev_cache import CachingAcquisition, DevCache
from rastro.browser import job_acquisition
from rastro.domain.jobs import Job
from rastro.domain.results import ExtractionResult
from rastro.ports.acquisition import AcquisitionPort

CACHE_DIR = ".rastro-studio/cache"
MAX_SAMPLES = 5
MAX_VALUE = 120
READ_OUTCOMES = frozenset({"fetched", "no_records", "partial"})
BUDGET_STOPS = frozenset({"spider_time_budget"})


def dev_acquisition(
    job: Job, supplied: AcquisitionPort | None, root: Path
) -> AcquisitionPort | None:
    """The acquirer a run would use, behind the development cache; none for local files."""
    inner = job_acquisition(job, supplied)
    if inner is None and job.acquisition is not None:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer

        inner = WebAcquirer(job.acquisition, transport=CurlTransport(), resolver=SystemResolver())
    return CachingAcquisition(inner, DevCache(root / CACHE_DIR)) if inner is not None else None


def pages_read(result: ExtractionResult[Any]) -> int:
    """Pages this source read: the crawl report's fetched pages, else its one page."""
    if result.spider is not None:
        return sum(1 for page in result.spider.pages if page.outcome in READ_OUTCOMES)
    return 1


def sample_rows(result: ExtractionResult[Any], room: int) -> list[dict[str, str]]:
    rows = []
    for record in result.records[:room]:
        data = record.data.model_dump(mode="json")
        rows.append({str(k): str(v)[:MAX_VALUE] for k, v in data.items() if v is not None})
    return rows


@dataclass
class LiveRun:
    file: str
    sources_total: int
    budget_seconds: int | None
    clock: Callable[[], float]
    started: float = 0.0
    finished: float | None = None
    state: str = "running"  # running | done | stopped | failed
    sources_done: int = 0
    pages: int = 0
    records: int = 0
    failed_sources: int = 0
    will_continue: bool = False
    error: dict[str, Any] | None = None
    sample: list[dict[str, str]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.started = self.clock()

    def add(self, result: ExtractionResult[Any]) -> None:
        self.sources_done += 1
        self.pages += pages_read(result)
        self.records += len(result.records)
        self.failed_sources += str(result.status).endswith("failure")
        self.sample.extend(sample_rows(result, MAX_SAMPLES - len(self.sample)))
        self.will_continue = self.will_continue or result.pagination_stop in BUDGET_STOPS

    def finish(self, state: str, error: dict[str, Any] | None = None) -> None:
        self.state, self.error, self.finished = state, error, self.clock()

    def snapshot(self) -> dict[str, Any]:
        end = self.finished if self.finished is not None else self.clock()
        return {
            "ok": True,
            "state": self.state,
            "file": self.file,
            "seconds_used": round(max(0.0, end - self.started), 1),
            "budget_seconds": self.budget_seconds,
            "sources_done": self.sources_done,
            "sources_total": self.sources_total,
            "pages": self.pages,
            "records": self.records,
            "failed_sources": self.failed_sources,
            "will_continue": self.will_continue,
            "sample": self.sample,
            "error": self.error,
        }
