"""What an unbounded crawl adds to the spider: time budget, throttle, checkpoint and note.

An unbounded crawl (``max_pages = "unbounded"``) is bounded by ``time_budget_seconds``
instead of a page count or the request budget. It works through its frontier in small
chunks, checks the clock between them, and when time or the record limit runs out it saves
the frontier to its checkpoint (see ``crawl_checkpoint``) so the next run resumes there.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from time import monotonic, perf_counter
from typing import Literal

from pydantic import BaseModel

from rastro.application.crawl_checkpoint import CheckpointStore, CrawlCheckpoint
from rastro.application.spider_links import Link, describe_url
from rastro.domain.models import ExtractionResult, Issue, Limits, Outcome, Source
from rastro.domain.provenance_url import canonical_page_url
from rastro.domain.source_notes import CrawlNote, SourceNotes, SourceReason
from rastro.ports.engine import CrawlThrottle

__all__ = [
    "CHUNK_PER_WORKER",
    "UNBOUNDED",
    "CrawlCheckpointing",
    "CrawlRuntime",
    "Stop",
    "rebase_on_start",
    "records_full",
    "with_crawl_note",
]

UNBOUNDED = 2**62  # the page and request count of an unbounded crawl: time bounds it
CHUNK_PER_WORKER = 2  # pages fetched between two looks at the clock, per worker
SAVE_EVERY_SECONDS = 60.0
"""How often an unbounded crawl rewrites its checkpoint while it runs."""
type Stop = Literal["time_budget", "record_limit"]
type CheckpointState = Literal["none", "saved", "finished", "discarded"]
_BLOCKED_STATUS = frozenset({429, 503})
_STOPS: dict[str, SourceReason] = {
    "spider_page_limit": "page_cap",
    "spider_depth_limit": "depth_cap",
    "spider_budget": "request_budget",
    "spider_page_failed": "failed",
    "spider_time_budget": "time_budget",
    "record_limit": "stopped",
}


def _now() -> float:
    return monotonic()


@dataclass(frozen=True)
class CrawlRuntime:
    """The clock, the pause and the optional adaptive throttle a crawl runs with."""

    clock: Callable[[], float] = field(default=_now)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    throttle: CrawlThrottle | None = None

    async def paced[T: BaseModel](
        self, host: str, floor: float, fetch: Callable[[], Awaitable[ExtractionResult[T]]]
    ) -> ExtractionResult[T]:
        """Wait what the throttle asks beyond the host's own interval, fetch, then report."""
        if self.throttle is None:
            return await fetch()
        extra = self.throttle.delay_for(host, floor) - floor
        if extra > 0:
            await self.sleep(extra)
        started = perf_counter()
        result = await fetch()
        self.throttle.record(
            host,
            latency=perf_counter() - started,
            ok=not _blocked(result),
            retry_after=result.retry_after_seconds,
        )
        return result


def _blocked[T: BaseModel](result: ExtractionResult[T]) -> bool:
    if result.http_status in _BLOCKED_STATUS or result.status == Outcome.ACCESS_RESTRICTION:
        return True
    return any(issue.code == "rate_limited" for issue in result.issues)


@dataclass
class CrawlCheckpointing:
    """Resume from, then save or clear, one unbounded crawl's checkpoint."""

    store: CheckpointStore | None
    resumed: bool = False
    discarded: bool = False
    saved_at: float | None = None

    def progress(
        self,
        now: float,
        fetched: Iterable[tuple[int, Link]],
        queued: Iterable[tuple[int, int, Link]],
        seen: set[str],
    ) -> None:
        """Save every ``SAVE_EVERY_SECONDS`` while crawling, so a crash loses little.

        The pages this run fetched go back into the saved frontier: their records are only
        stored when the run ends, so a run that dies refetches them instead of losing them.
        """
        if self.store is None:
            return
        if self.saved_at is None:
            self.saved_at = now
        elif now - self.saved_at >= SAVE_EVERY_SECONDS:
            self.saved_at = now
            frontier = [(d, link.url) for d, link in fetched]
            frontier += [(d, link.url) for _, d, link in queued]
            self.store.save(frontier, sorted(seen))

    def load(self) -> CrawlCheckpoint | None:
        if self.store is None:
            return None
        loaded = self.store.load()
        self.discarded = loaded.discarded
        return loaded.checkpoint

    def close(self, frontier: list[tuple[int, str]], seen: set[str]) -> CheckpointState:
        """Save what is left, or clear a finished crawl's checkpoint."""
        if self.store is None:
            return "none"
        if frontier:
            return "saved" if self.store.save(frontier, sorted(seen)) else "discarded"
        cleared = self.store.clear()
        if self.resumed or cleared:
            return "finished"
        return "discarded" if self.discarded else "none"

    def issues(self) -> tuple[Issue, ...]:
        found = [Issue(code="crawl_resumed")] if self.resumed else []
        if self.discarded:
            found.append(Issue(code="crawl_checkpoint_discarded"))
        return tuple(found)


def with_crawl_note[T: BaseModel](
    result: ExtractionResult[T],
    *,
    unbounded: bool,
    pages: int,
    frontier: int,
    checkpointing: CrawlCheckpointing,
    checkpoint: CheckpointState,
) -> ExtractionResult[T]:
    """Attach the crawl's note, and its resume or discard issues, to the combined result."""
    note = CrawlNote(
        mode="unbounded" if unbounded else "bounded",
        pages_fetched=pages,
        frontier_remaining=frontier,
        resumed=checkpointing.resumed,
        checkpoint=checkpoint,
        stop=_STOPS.get(result.pagination_stop or "", "complete"),
    )
    notes = (result.notes or SourceNotes()).model_copy(update={"crawl": note})
    issues = (*result.issues, *checkpointing.issues())
    return result.model_copy(update={"notes": notes, "issues": issues})


def records_full[T: BaseModel](
    pages: list[ExtractionResult[T]],
    links: list[tuple[int, Link]],
    limits: Limits,
    queue: deque[tuple[int, int, Link]],
) -> bool:
    """At the record limit, pages past it go back to the frontier for the next run.

    ``links`` holds the depth and link of each page; the first page always stays.
    """
    total = sum(len(page.records) for page in pages)
    if total < limits.max_records:
        return False
    while total > limits.max_records and len(pages) > 1:
        total -= len(pages.pop().records)
        depth, link = links.pop()
        queue.appendleft((0, depth, link))
    return True


def rebase_on_start[T: BaseModel](
    result: ExtractionResult[T], source: Source
) -> ExtractionResult[T]:
    """A resumed crawl's records belong to its start URL, like the first run's did."""
    url = describe_url(source.url).url
    start = Source.model_validate({**source.model_dump(), "url": url}).evidence_url
    records = tuple(
        record.model_copy(
            update={
                "evidence": {
                    name: evidence.model_copy(
                        update={
                            "source_url": start,
                            "page_url": evidence.page_url
                            or canonical_page_url(evidence.source_url),
                        }
                    )
                    if evidence.source_url != start
                    else evidence
                    for name, evidence in record.evidence.items()
                }
            }
        )
        for record in result.records
    )
    return result.model_copy(
        update={"records": records, "source_url": start, "requested_url": start}
    )
