"""Bounded next-page traversal through the existing parser and acquisition ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urldefrag, urljoin, urlsplit

from pydantic import BaseModel, ValidationError

from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    PaginationScope,
    pagination_boundary,
)
from rastro.application.outcomes import failure_result, stamp_acquired
from rastro.application.pagination_records import combine_pages
from rastro.application.scroll import pages_left, spend_page
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    ExtractionResult,
    JsonLdRecipe,
    JsonRecipe,
    Limits,
    Outcome,
    Recipe,
    ResourceLimitError,
    Source,
)
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.parser import DocumentParser


def next_url(body: str, recipe: Recipe, parser: DocumentParser, limits: Limits) -> str | None:
    """Read one unambiguous next URL with the same bounded parser used for records."""
    assert recipe.pagination is not None
    expression = recipe.pagination.next
    if isinstance(recipe, CssRecipe):
        probe: Recipe = CssRecipe(record_selector="html", fields={"next": expression})
    elif isinstance(recipe, JsonRecipe):
        parent, _, key = expression.rpartition("/")
        probe = JsonRecipe(record_path=parent, fields={"next": f"/{key}"})
    else:
        probe = JsonLdRecipe(fields={"next": expression})
    parsed = parser.parse(body, probe, limits)
    values = []
    if any(issue.code != "record_path_not_found" for issue in parsed.issues) or any(
        item.issues for item in parsed.candidates
    ):
        raise ConfigurationError("Invalid pagination cursor")
    for item in parsed.candidates:
        value = item.values.get("next")
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > 8192:
            raise ConfigurationError("Invalid pagination cursor")
        values.append(value)
    if len(set(values)) > 1:
        raise ConfigurationError("Ambiguous pagination cursor")
    return values[0] if values else None


async def extract_paginated[T: BaseModel](
    *,
    source: Source,
    schema: type[T],
    recipe: Recipe,
    parser: DocumentParser,
    limits: Limits,
    acquisition: AcquisitionPort,
    policy: AcquisitionPolicy | None,
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
) -> ExtractionResult[T]:
    """Keep page one's memory/repair evidence and combine later pages in acquisition order."""
    check_untrusted_recipe(recipe)
    shared = PAGINATION_SCOPE.get()
    if shared is not None:  # inside a spider run: one budget for every page of the source
        return await _traverse(
            source, schema, recipe, parser, limits, acquisition, shared, extract_page
        )
    scope = PaginationScope(
        urlsplit(source.url).hostname,
        urlsplit(source.url).scheme == "https",
        policy.max_requests if policy else 30,
    )
    token = PAGINATION_SCOPE.set(scope)
    try:
        return await _traverse(
            source, schema, recipe, parser, limits, acquisition, scope, extract_page
        )
    finally:
        PAGINATION_SCOPE.reset(token)


async def _traverse[T: BaseModel](
    source: Source,
    schema: type[T],
    recipe: Recipe,
    parser: DocumentParser,
    limits: Limits,
    acquisition: AcquisitionPort,
    scope: PaginationScope,
    extract_page: Callable[[AcquisitionResult], Awaitable[ExtractionResult[T]]],
) -> ExtractionResult[T]:
    assert recipe.pagination is not None
    pages: list[ExtractionResult[T]] = []
    visited: set[str] = set()
    current = source
    stop: str | None = "page_failure"
    for _ in range(recipe.pagination.max_pages):
        visited.add(urldefrag(current.url)[0])
        started, started_at = perf_counter(), datetime.now(UTC)
        remaining = scope.remaining
        fetched = await acquisition.acquire(
            current,
            max_bytes=limits.max_input_bytes(recipe.representation),
            representation=recipe.representation,
        )
        scope.remaining = min(scope.remaining, remaining - fetched.requests)
        try:
            result = await extract_page(fetched)
        except (ConfigurationError, ResourceLimitError):
            if not pages:
                raise
            result = failure_result(
                status=Outcome.PERMANENT_FAILURE,
                issue_code="pagination_page_failed",
                source=source,
                schema=schema,
                started_at=started_at,
            )
        ended = bool(pages) and _is_empty_end(fetched, result)
        if ended:  # An empty 200 page after real pages is the end of the list, not a failure.
            result = result.model_copy(update={"status": Outcome.SUCCESS})
        pages.append(
            stamp_acquired(
                result, source=current, fetched=fetched, started=started, started_at=started_at
            )
        )
        stop = "empty_page" if ended else _page_stop(fetched, result)
        if sum(len(page.records) for page in pages) > limits.max_records:
            stop = "record_limit"
        if stop:
            break
        visited.add(urldefrag(fetched.url)[0])
        try:
            cursor = next_url(fetched.body or "", recipe, parser, limits)
            if cursor is None:
                stop = "no_next"
                break
            target = urldefrag(urljoin(fetched.url, cursor))[0]
            following = Source.model_validate({**source.model_dump(), "url": target})
            target = following.url
            stop = pagination_boundary(target, urlsplit(source.url).hostname, scope.secure)
            if not stop and target in visited:
                stop = "repeat"
            if not stop and len(pages) >= recipe.pagination.max_pages:
                stop = "max_pages"
            if not stop and not pages_left():
                stop = "max_pages"  # the crawl's page cap counts pagination documents too
            if not stop and scope.remaining <= 0:
                stop = "budget"
            if stop:
                break
            spend_page()
            current = following
        except (ValueError, ValidationError, ConfigurationError, ResourceLimitError):
            stop = "invalid_next"
            break
    return combine_pages(pages, stop or "max_pages", limits)


def _is_empty_end[T: BaseModel](fetched: AcquisitionResult, result: ExtractionResult[T]) -> bool:
    return (
        fetched.http_status == 200
        and result.status == Outcome.MISSING_DATA
        and not result.records
        and not result.issues
    )


def _page_stop[T: BaseModel](fetched: AcquisitionResult, result: ExtractionResult[T]) -> str | None:
    if fetched.issue_code == "request_limit":
        return "budget"
    if fetched.issue_code in {"off_host", "https_downgrade", "invalid_next"}:
        return fetched.issue_code
    # A page whose scroll hit a cap is partial but real: later pages are still collected.
    scrolled = result.status == Outcome.PARTIAL_SUCCESS and all(
        issue.code.startswith("scroll_") for issue in result.issues
    )
    return None if result.status == Outcome.SUCCESS or scrolled else "page_failure"
