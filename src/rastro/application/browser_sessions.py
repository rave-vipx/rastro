"""One browser context per source per run, closed when the source finishes (N2, ADR 0034).

``source_session()`` opens a scope. The first rendered page of each renderer opens its
session; later pages and scroll steps of that source reuse it. Nothing is shared across
hosts or across sources: a different host, or a renderer without sessions, renders per page.
Nested scopes reuse the outer one, so a spider can wrap a whole crawl of one start source.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from rastro.domain.acquisition import AcquisitionPolicy, PinnedTarget
from rastro.ports.browser import BrowserSession, BrowserSessions


@dataclass
class _Entry:
    host: str
    session: BrowserSession
    rendered: str | None = None  # the URL the session's page shows; ``None`` while unknown


class SourceSessions:
    """The sessions one source opened, keyed by renderer and bound to the first host."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._open: dict[int, _Entry] = {}

    async def reuse(
        self,
        renderer: BrowserSessions,
        target: PinnedTarget,
        targets: tuple[PinnedTarget, ...],
        policy: AcquisitionPolicy,
    ) -> BrowserSession | None:
        """The renderer's session for ``target``'s host; ``None`` for any other host."""
        entry = self._open.get(id(renderer))
        if entry is None:
            session = await self._stack.enter_async_context(
                renderer.session(targets, policy=policy)
            )
            self._open[id(renderer)] = _Entry(target.host, session)
            return session
        return entry.session if entry.host == target.host else None

    def mark_rendered(self, renderer: object, url: str | None) -> None:
        """Record what the renderer's session page shows now (``None``: unknown, mid-render)."""
        entry = self._open.get(id(renderer))
        if entry is not None:
            entry.rendered = url

    def showing(self, renderer: object, *, host: str, url: str) -> BrowserSession | None:
        """The session only while bound to ``host`` and last rendered exactly ``url``.

        A scroll step acts on the session's one page, so it may only follow the page it
        belongs to: not one rendered in a fresh context, nor one rendered after it.
        """
        entry = self._open.get(id(renderer))
        if entry is None or entry.host != host or entry.rendered != url:
            return None
        return entry.session

    async def close(self) -> None:
        self._open.clear()
        await self._stack.aclose()


SOURCE_SESSIONS: ContextVar[SourceSessions | None] = ContextVar("source_sessions", default=None)


@asynccontextmanager
async def source_session() -> AsyncIterator[SourceSessions]:
    """Scope one source's browser context; closes it even when the source fails."""
    outer = SOURCE_SESSIONS.get()
    if outer is not None:
        yield outer
        return
    scope = SourceSessions()
    token = SOURCE_SESSIONS.set(scope)
    try:
        yield scope
    finally:
        SOURCE_SESSIONS.reset(token)
        await scope.close()
