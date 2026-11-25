"""The spider's frontier: which links are new, which are refused, and the report rows.

Discovered links are deduplicated by a digest of their page identity, so the same set can
be saved in a checkpoint without naming any visited query. Every refused link becomes a
report row; rows are capped and every page is counted by outcome.
"""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from typing import Final

from rastro.application.acquisition_state import PaginationScope
from rastro.application.crawl_checkpoint import CrawlCheckpoint, seen_key
from rastro.application.spider_filter import link_refusal
from rastro.application.spider_links import Link, classify_link, report_path
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.spider import SpiderPolicy
from rastro.domain.spider_report import (
    MAX_REPORTED_PAGES,
    SpiderOutcome,
    SpiderPage,
    SpiderReport,
)

__all__ = ["CrawlRows", "Frontier", "Queued", "in_order"]

_NOTE_CAP: Final = 500  # refused-link rows; a bounded crawl's fetched pages always fit after
type Queued = tuple[int, int, Link]  # ordinal, depth, link


class CrawlRows:
    """The report rows of one crawl and the caps it hit."""

    def __init__(self) -> None:
        self.rows: dict[int, SpiderPage] = {}
        self.hit: set[str] = set()
        self.unreported = 0
        self.outcomes: Counter[SpiderOutcome] = Counter()

    def note(self, ordinal: int, depth: int, link: Link, outcome: SpiderOutcome) -> None:
        """A link that is not fetched, and why."""
        if outcome in ("budget", "page_limit"):
            self.hit.add(outcome)
        page = SpiderPage(
            ordinal=ordinal, depth=depth, host=link.host, path=report_path(link), outcome=outcome
        )
        self._row(page, _NOTE_CAP)

    def fetched(
        self, ordinal: int, depth: int, link: Link, outcome: SpiderOutcome, records: int
    ) -> None:
        """A page that was fetched."""
        page = SpiderPage(
            ordinal=ordinal,
            depth=depth,
            host=link.host,
            path=report_path(link),
            outcome=outcome,
            records=records,
        )
        self._row(page, MAX_REPORTED_PAGES)

    def report(self, discovered: int) -> SpiderReport:
        return SpiderReport(
            pages=tuple(self.rows[key] for key in sorted(self.rows)),
            discovered=discovered,
            unreported=self.unreported,
            outcomes=dict(self.outcomes) if self.unreported else {},
        )

    def _row(self, page: SpiderPage, cap: int) -> None:
        self.outcomes[page.outcome] += 1
        if len(self.rows) >= cap:
            self.unreported += 1
        else:
            self.rows[page.ordinal] = page


class Frontier:
    """Admit each newly discovered link to the queue, or note why it is refused."""

    def __init__(
        self,
        base: str,
        spider: SpiderPolicy,
        scope: PaginationScope,
        policy: AcquisitionPolicy | None,
        rows: CrawlRows,
    ) -> None:
        self.base, self.spider, self.scope, self.policy, self.rows = (
            base,
            spider,
            scope,
            policy,
            rows,
        )
        self.seen: set[str] = set()
        self.ordinal = 1  # the start URL

    def discover(
        self,
        hrefs: list[str],
        depth: int,
        into: list[Queued] | deque[Queued],
        *,
        paths: bool = True,
    ) -> None:
        """Queue each new link found for ``depth``, or note why it is not fetched."""
        for href in hrefs:
            link = classify_link(self.base, href)
            key = seen_key(link.url) if not link.refusal else ""
            if key in self.seen:
                continue
            self.ordinal += 1
            refusal: SpiderOutcome | None = link.refusal or link_refusal(
                link, self.scope, self.spider, self.policy, paths=paths
            )
            if refusal is None and depth > self.spider.max_depth:
                refusal = "depth_limit"
                self.rows.hit.add("depth_limit")
            if refusal is not None:
                self.rows.note(self.ordinal, depth, link, refusal)
            else:
                into.append((self.ordinal, depth, link))
            if key:
                self.seen.add(key)

    def resume(self, checkpoint: CrawlCheckpoint) -> deque[Queued]:
        """The saved frontier, each link checked again as if just discovered."""
        self.seen.update(checkpoint.seen)
        queue: deque[Queued] = deque()
        for entry in checkpoint.frontier:
            link = classify_link(self.base, entry.url)
            self.ordinal += 1
            refusal = link.refusal or link_refusal(link, self.scope, self.spider, self.policy)
            if refusal is None and entry.depth <= self.spider.max_depth:
                queue.append((self.ordinal, entry.depth, link))
            else:
                self.rows.note(self.ordinal, entry.depth, link, refusal or "depth_limit")
        return queue


async def in_order[R](
    batch: list[tuple[int, Link]],
    workers: int,
    fetch: Callable[[int, Link], Awaitable[R]],
) -> list[R]:
    """Fetch ``batch`` with at most ``workers`` at once; results keep the batch order."""
    results: list[R | None] = [None] * len(batch)
    pending = iter(enumerate(batch))

    async def worker() -> None:
        for index, (ordinal, link) in pending:
            results[index] = await fetch(ordinal, link)

    if workers == 1 or len(batch) <= 1:
        await worker()
    else:
        async with asyncio.TaskGroup() as group:
            for _ in range(min(len(batch), workers)):
                group.create_task(worker())
    return [item for item in results if item is not None]
