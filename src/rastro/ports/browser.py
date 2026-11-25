"""Optional rendered-document capability, independent of browser libraries."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, PinnedTarget
from rastro.domain.models import Source


@runtime_checkable
class BrowserRenderer(Protocol):
    async def render(
        self,
        target: PinnedTarget,
        *,
        policy: AcquisitionPolicy,
        max_bytes: int,
        targets: tuple[PinnedTarget, ...],
    ) -> AcquisitionResult: ...


class BrowserSession(Protocol):
    """One browser context for one source: pages and scroll steps share its cookies and page."""

    async def render(
        self, target: PinnedTarget, *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult: ...

    async def scroll(
        self, *, policy: AcquisitionPolicy, max_bytes: int, settle_ms: int
    ) -> AcquisitionResult:
        """Scroll the current page once, wait at most ``settle_ms``, return its document."""
        ...


@runtime_checkable
class BoxMeasuringSession(Protocol):
    """Optional: a session that can say where matched elements sit on its rendered page."""

    async def element_boxes(
        self, selectors: Sequence[str]
    ) -> Mapping[str, Sequence[Mapping[str, float]]]: ...


@runtime_checkable
class BrowserSessions(Protocol):
    """A renderer that can keep one context open for a source (a separate, optional protocol)."""

    def session(
        self, targets: tuple[PinnedTarget, ...], *, policy: AcquisitionPolicy
    ) -> AbstractAsyncContextManager[BrowserSession]: ...


@runtime_checkable
class ScrollingAcquisition(Protocol):
    """An acquisition port that can scroll the page it last rendered for the current source."""

    def scrolls(self, url: str) -> bool:
        """Whether ``url`` is rendered by a browser (HTTP sources cannot scroll)."""
        ...

    async def scroll(self, source: Source, *, settle_ms: int, max_bytes: int) -> AcquisitionResult:
        """One scroll step inside the source's open context."""
        ...


@runtime_checkable
class ScrollSkipReason(Protocol):
    """Optional: the issue code to report when ``scrolls(url)`` is false (a separate protocol)."""

    def scroll_skip_code(self, url: str) -> str: ...


@runtime_checkable
class BrowserRendered(Protocol):
    """Optional: whether ``url`` is rendered in one shared browser page (a separate protocol)."""

    def renders_in_browser(self, url: str) -> bool:
        """True when fetches of this source share one page and must not overlap."""
        ...


@runtime_checkable
class BrowserMayRender(Protocol):
    """Optional: whether any page of a crawl from ``url`` may render in a browser (separate)."""

    def may_render_in_browser(self, url: str) -> bool:
        """True when a page reached from ``url`` can share the source's browser page."""
        ...
