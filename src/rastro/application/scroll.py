"""Bounded scroll: re-extract after each scroll step until nothing new appears (N9, ADR 0033).

Scroll runs through the source's open browser context (``browser_sessions``), spends the same
request budget as pagination, and never changes what the operator granted: it does not enable
stealth, proxies or challenge solving. Hitting a cap is a partial success that keeps every
accepted record.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel

from rastro.application.acquisition_state import PAGINATION_SCOPE, PaginationScope
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import (
    ConfigurationError,
    ExtractionRecord,
    ExtractionResult,
    Issue,
    Limits,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.domain.scroll import ScrollSpec
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.browser import ScrollingAcquisition

ExtractPage = Callable[[AcquisitionResult], Awaitable[ExtractionResult[BaseModel]]]
# A normal end of the list; every other stop is a cap or a failure and is partial.
END = "no_new_records"


@dataclass
class PageCounter:
    """Pages a crawl may still fetch; scroll steps spend it, so the tighter cap wins."""

    remaining: int


PAGE_COUNTER: ContextVar[PageCounter | None] = ContextVar("page_counter", default=None)


@contextmanager
def scroll_scope(source: Source, policy: AcquisitionPolicy | None) -> Iterator[None]:
    """The request budget of a scrolled source that is not paginated (pagination has its own)."""
    if PAGINATION_SCOPE.get() is not None:
        yield
        return
    parts, budget = urlsplit(source.url), (policy.max_requests if policy else 30)
    token = PAGINATION_SCOPE.set(
        PaginationScope(parts.hostname, parts.scheme == "https", budget, budget)
    )
    try:
        yield
    finally:
        PAGINATION_SCOPE.reset(token)


def _key[T: BaseModel](record: ExtractionRecord[T]) -> str:
    return record.data.model_dump_json()


def _spend_page() -> bool:
    counter = PAGE_COUNTER.get()
    if counter is None:
        return True
    if counter.remaining <= 0:
        return False
    counter.remaining -= 1
    return True


def scrolled[T: BaseModel](
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
    *,
    spec: ScrollSpec,
    acquisition: AcquisitionPort,
    limits: Limits,
    owns_scope: bool,
) -> Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]]:
    """Wrap one source's page extraction so each page is scrolled until nothing is new."""
    noted = False

    async def run(fetched: AcquisitionResult) -> ExtractionResult[T]:
        nonlocal noted
        first = await extract_page(fetched)
        if fetched.status != Outcome.SUCCESS or first.status != Outcome.SUCCESS:
            return first
        if not (isinstance(acquisition, ScrollingAcquisition) and acquisition.scrolls(fetched.url)):
            if noted:
                return first
            noted = True
            return first.model_copy(
                update={"issues": (*first.issues, Issue(code="scroll_ignored_http"))}
            )
        scope = PAGINATION_SCOPE.get()
        if owns_scope and scope is not None:
            scope.remaining = min(scope.remaining, scope.total - fetched.requests)
        return await _scroll(first, fetched, extract_page, spec, acquisition, limits, scope)

    return run


async def _scroll[T: BaseModel](
    first: ExtractionResult[T],
    fetched: AcquisitionResult,
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
    spec: ScrollSpec,
    acquisition: ScrollingAcquisition,
    limits: Limits,
    scope: PaginationScope | None,
) -> ExtractionResult[T]:
    records = list(first.records)
    seen = Counter(_key(record) for record in records)
    rejected, requests, stop = first.metrics.rejected, 0, "max_steps"
    source = Source(url=fetched.url)
    for _ in range(spec.max_steps):
        if (scope is not None and scope.remaining <= 0) or not _spend_page():
            stop = "budget"
            break
        before = scope.remaining if scope else 0
        try:
            step = await acquisition.scroll(
                source, settle_ms=spec.settle_ms, max_bytes=limits.max_input_bytes("html")
            )
        except ConfigurationError:  # a renderer that cannot keep a session cannot scroll
            stop = "step_failed"
            break
        requests += max(1, step.requests)
        if scope is not None:
            scope.remaining = min(scope.remaining, before - max(1, step.requests))
        if step.status != Outcome.SUCCESS:
            stop = "budget" if step.issue_code == "request_limit" else "step_failed"
            break
        try:
            result = await extract_page(step)
        except (ConfigurationError, ResourceLimitError):
            stop = "step_failed"
            break
        if result.status != Outcome.SUCCESS:
            stop = "step_failed"
            break
        fresh = _new_records(result.records, seen)
        if result.metrics.rejected > rejected:
            rejected, stop = result.metrics.rejected, "records_rejected"
            records.extend(fresh)
            break
        if not fresh:
            stop = END
            break
        records.extend(fresh)
        if len(records) > limits.max_records:
            records, stop = records[: limits.max_records], "record_limit"
            break
    return _merged(first, records, stop, rejected, requests)


def _new_records[T: BaseModel](
    records: tuple[ExtractionRecord[T], ...], seen: Counter[str]
) -> list[ExtractionRecord[T]]:
    """Records beyond what earlier steps already saw; genuine repeats on one page still count."""
    here: Counter[str] = Counter()
    fresh: list[ExtractionRecord[T]] = []
    for record in records:
        key = _key(record)
        here[key] += 1
        if here[key] > seen[key]:
            fresh.append(record)
    seen.update({key: count - seen[key] for key, count in here.items() if count > seen[key]})
    return fresh


def _merged[T: BaseModel](
    first: ExtractionResult[T],
    records: list[ExtractionRecord[T]],
    stop: str,
    rejected: int,
    requests: int,
) -> ExtractionResult[T]:
    ended = stop == END
    metrics = first.metrics.model_copy(
        update={"accepted": len(records), "rejected": rejected, "request_count": requests}
    )
    return first.model_copy(
        update={
            "records": tuple(records),
            "metrics": metrics,
            "status": Outcome.SUCCESS if ended else Outcome.PARTIAL_SUCCESS,
            "issues": first.issues if ended else (*first.issues, Issue(code=f"scroll_{stop}")),
        }
    )
