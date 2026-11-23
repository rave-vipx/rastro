"""Recipes over the site's own data: JSON responses a browser page loaded, captured bounded.

The job's ``acquisition.browser.capture_network`` (operator-granted ``network_capture``)
decides what may be captured: XHR or fetch responses with a JSON media type, from an allowed
host, whose path (never the query) matches one of its patterns, within a count and byte
budget. Captured bodies live in memory for the run only. A ``JsonRecipe`` with ``capture``
reads the first matching response; the job URL stays the page, so provenance, robots and
pacing are the page's. No match is ``capture_empty``, never an empty success.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from rastro.application.browser_sessions import source_session
from rastro.application.capture_buffer import (
    MAX_CAPTURED_BYTES,
    MAX_CAPTURED_RESPONSES,
    MAX_RESPONSE_BYTES,
    CaptureBuffer,
    CaptureTicket,
    is_json_media,
)
from rastro.application.capture_offers import (
    CaptureOffer,
    capture_offers,
    offer_policy,
    recipe_from_capture,
)
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.capture_pattern import capture_matches
from rastro.domain.models import ConfigurationError, Outcome, Source
from rastro.ports.engine import CapturedResponse, NetworkCapture

__all__ = [
    "MAX_CAPTURED_BYTES",
    "MAX_CAPTURED_RESPONSES",
    "MAX_RESPONSE_BYTES",
    "CaptureBuffer",
    "CaptureOffer",
    "CaptureTicket",
    "acquire_captured",
    "capturable_json",
    "capture_offers",
    "captured_document",
    "collecting",
    "is_json_media",
    "offer_policy",
    "recipe_from_capture",
    "recipe_session",
    "record_captured",
    "selecting_capture",
]

_RUN: ContextVar[list[CapturedResponse] | None] = ContextVar("captured_run", default=None)
_SELECTION: ContextVar[str | None] = ContextVar("capture_selection", default=None)


def record_captured(session: object) -> None:
    """Add what ``session`` captured on its last render to the running capture, if any."""
    run = _RUN.get()
    if run is not None and isinstance(session, NetworkCapture):
        room = MAX_CAPTURED_RESPONSES - len(run)
        run.extend(session.captured()[: max(0, room)])


@contextmanager
def collecting() -> Iterator[list[CapturedResponse]]:
    """Collect the responses captured by the renders inside this block."""
    run: list[CapturedResponse] = []
    token = _RUN.set(run)
    try:
        yield run
    finally:
        _RUN.reset(token)


@asynccontextmanager
async def recipe_session(recipe: object) -> AsyncIterator[None]:
    """One source's browser session, reading the response the recipe's ``capture`` names."""
    with selecting_capture(recipe):
        async with source_session():
            yield


@contextmanager
def selecting_capture(recipe: object) -> Iterator[None]:
    """Let the browser acquisition read the response the recipe's ``capture`` names."""
    token = _SELECTION.set(getattr(recipe, "capture", None))
    try:
        yield
    finally:
        _SELECTION.reset(token)


def captured_document(
    page: AcquisitionResult, responses: tuple[CapturedResponse, ...], pattern: str | None
) -> AcquisitionResult:
    """The page result carrying the first successful response ``pattern`` matches."""
    if page.status != Outcome.SUCCESS:
        return page
    for response in responses:
        if 200 <= response.status < 300 and (
            pattern is None or capture_matches(pattern, response.path)
        ):
            return replace(page, body=response.body.decode("utf-8", errors="replace"))
    return replace(page, body=None, status=Outcome.INVALID_DATA, issue_code="capture_empty")


async def acquire_captured(
    policy: AcquisitionPolicy, fetch_page: Callable[[], Awaitable[AcquisitionResult]]
) -> AcquisitionResult:
    """Render the page in one source session and return the captured JSON it loaded."""
    if not policy.browser.capture_network:
        raise ConfigurationError(
            "A browser JSON source reads a captured response: set acquisition.browser."
            "capture_network"
        )
    with collecting() as run:
        async with source_session():  # capture needs a session that can report it
            page = await fetch_page()
    return captured_document(page, tuple(run), _SELECTION.get())


async def capturable_json(
    acquirer: Any, source: Source, *, max_bytes: int
) -> tuple[CaptureOffer, ...]:
    """The JSON responses ``source``'s page loads, as offers to build a recipe over.

    ``acquirer`` is a browser acquisition whose policy captures (see ``offer_policy``); the
    page is fetched under its usual robots, pacing and host rules.
    """
    with collecting() as run:
        async with source_session():
            page = await acquirer.acquire(source, max_bytes=max_bytes)
    return capture_offers(tuple(run)) if page.status == Outcome.SUCCESS else ()
