"""Capture a page's XHR and fetch JSON responses into a bounded buffer; read open shadow roots.

The listener attaches once per page and captures only while a render is running, into that
render's ``CaptureBuffer``. It looks at responses the network guard already let through,
reads a body only after the buffer accepted the response's head, and never keeps the URL's
query, the request or any header.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

from rastro.application.capture_buffer import CaptureBuffer, CaptureTicket
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.browser_controls import BrowserControls
from rastro.ports.engine import CapturedResponse

__all__ = ["DRAIN_SECONDS", "CaptureListener", "page_document", "with_capture"]

DRAIN_SECONDS = 5.0
"""How long a finished render waits for body reads still in flight."""
_KINDS = frozenset({"xhr", "fetch"})

# Serialize the document with every open shadow root as a declarative template.
_SHADOW_HTML = """() => {
  const roots = [];
  const walk = (node) => {
    for (const el of node.querySelectorAll('*')) {
      if (el.shadowRoot) { roots.push(el.shadowRoot); walk(el.shadowRoot); }
    }
  };
  walk(document);
  return '<!DOCTYPE html>' + document.documentElement.getHTML({shadowRoots: roots});
}"""


async def page_document(page: Any, controls: BrowserControls) -> str:
    """The page's HTML; with ``pierce_shadow`` it includes open shadow roots."""
    if controls.pierce_shadow:
        try:
            html = await page.evaluate(_SHADOW_HTML)
        except Exception:
            html = None
        if isinstance(html, str):
            return html
    return await page.content()  # type: ignore[no-any-return]


class CaptureListener:
    """One per browser session: the current render's buffer and the reads it started."""

    def __init__(self) -> None:
        self._buffer: CaptureBuffer | None = None
        self._pending: set[asyncio.Future[None]] = set()
        self._pages: weakref.WeakSet[Any] = weakref.WeakSet()
        self.last: tuple[CapturedResponse, ...] = ()

    async def attach(self, page: Any) -> None:
        """Listen on ``page`` once; usable as a page-setup hook."""
        if page not in self._pages:
            page.on("response", self._seen)
            self._pages.add(page)

    def start(self, policy: AcquisitionPolicy) -> None:
        """Capture for the render about to run, when its controls ask for it."""
        self.last = ()
        self._buffer = CaptureBuffer(policy) if policy.browser.capture_network else None

    async def finish(self) -> tuple[CapturedResponse, ...]:
        """Stop capturing, wait briefly for reads in flight, and keep what fit."""
        pending = set(self._pending)
        if pending:
            await asyncio.wait(pending, timeout=DRAIN_SECONDS)
        buffer, self._buffer = self._buffer, None
        for task in pending:
            task.cancel()
        if buffer is None:
            return ()
        buffer.close()
        self.last = buffer.responses
        return self.last

    def _seen(self, response: Any) -> None:
        buffer = self._buffer
        if buffer is None:
            return
        try:
            request = response.request
            if request.resource_type not in _KINDS:
                return
            headers = response.headers
            ticket = buffer.wants(
                response.url,
                request.method,
                response.status,
                str(headers.get("content-type", "")),
                headers.get("content-length"),
            )
        except Exception:
            return  # a response we cannot read the head of is not captured
        if ticket is not None:
            task = asyncio.ensure_future(self._read(buffer, ticket, response))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    @staticmethod
    async def _read(buffer: CaptureBuffer, ticket: CaptureTicket, response: Any) -> None:
        try:
            body = await response.body()
        except Exception:
            return
        if isinstance(body, bytes):
            buffer.add(ticket, body)


def with_capture(setup: Any, capture: CaptureListener | None) -> Any:
    """A page-setup hook running ``setup`` (the guard), then listening when capturing."""
    if capture is None:
        return setup

    async def both(page: Any) -> None:
        await setup(page)
        await capture.attach(page)

    return both
