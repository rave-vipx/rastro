"""The proxy route for the browser render in progress.

The browser acquirer sets it around each render, and the renderers read it when they launch
a browser or open a source's context. A context keeps the route it opened with for every
page of its source. The route carries credentials: never log or serialise it.
"""

from __future__ import annotations

from contextvars import ContextVar

from rastro.domain.acquisition import ProxyRoute

__all__ = ["BROWSER_PROXY"]

BROWSER_PROXY: ContextVar[ProxyRoute | None] = ContextVar("browser_proxy", default=None)
