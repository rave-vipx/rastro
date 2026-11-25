"""One browser context per source per run, closed when the source finishes (ADR 0034).

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
from urllib.parse import urlsplit

from rastro.domain.acquisition import AcquisitionPolicy, PinnedTarget
from rastro.ports.browser import BrowserSession, BrowserSessions


@dataclass
class _Entry:
    host: str
    session: BrowserSession
    stack: AsyncExitStack
    rendered: str | None = None  # the URL the session's page shows; ``None`` while unknown


class SourceSessions:
    """The sessions one source opened, keyed by renderer and bound to the first host."""

    def __init__(self) -> None:
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
            stack = AsyncExitStack()
            try:
                session = await stack.enter_async_context(renderer.session(targets, policy=policy))
            except BaseException:
                await stack.aclose()
                raise
            self._open[id(renderer)] = _Entry(target.host, session, stack)
            return session
        return entry.session if entry.host == target.host else None

    async def leave_foreign(self, renderer: object, url: str) -> bool:
        """Close the renderer's context when its page shows another host than the bound one.

        A context that held another host's document (a redirect) never serves the bound
        host again: the next page of that host opens a fresh one.
        """
        entry = self._open.get(id(renderer))
        if entry is None or _hostname(url) == _hostname(entry.host):
            return False
        del self._open[id(renderer)]
        await entry.stack.aclose()
        return True

    async def discard(self, renderer: object) -> None:
        """Close the renderer's context: after a failed step its page's host is unknown."""
        entry = self._open.pop(id(renderer), None)
        if entry is not None:
            await entry.stack.aclose()

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

    def last_rendered(self, renderer: object) -> BrowserSession | None:
        """The renderer's session when its last render succeeded on the bound host."""
        entry = self._open.get(id(renderer))
        return entry.session if entry is not None and entry.rendered is not None else None

    async def close(self) -> None:
        entries, self._open = list(self._open.values()), {}
        for entry in reversed(entries):
            await entry.stack.aclose()


def _hostname(url_or_host: str) -> str:
    host = urlsplit(url_or_host).hostname if "/" in url_or_host else url_or_host
    return (host or "").lower().rstrip(".")


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
