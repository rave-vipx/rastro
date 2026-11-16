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

from rastro.domain.acquisition import AcquisitionPolicy, PinnedTarget
from rastro.ports.browser import BrowserSession, BrowserSessions


class SourceSessions:
    """The sessions one source opened, keyed by renderer and bound to the first host."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._open: dict[int, tuple[str, BrowserSession]] = {}

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
            self._open[id(renderer)] = (target.host, session)
            return session
        return entry[1] if entry[0] == target.host else None

    def current(self, renderer: object) -> BrowserSession | None:
        entry = self._open.get(id(renderer))
        return entry[1] if entry else None

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
