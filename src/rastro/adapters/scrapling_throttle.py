"""``CrawlThrottle`` over the engine's adaptive per-host throttle (ADR 0040).

Only the delay arithmetic is used: Rastro's own frontier and acquisition stay in charge of
every request, so DNS pinning, SSRF checks, redirects and robots still apply. The delay
starts at the host's floor (the job's ``min_interval_seconds``), follows response latency,
doubles on a block, honours ``Retry-After`` and never exceeds ``max_delay``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rastro.ports.engine import CrawlThrottle

if TYPE_CHECKING:
    from scrapling.spiders.throttle import AutoThrottle

__all__ = ["ScraplingThrottle", "default_throttle"]


class ScraplingThrottle:
    """Adaptive per-host delay; the engine module is imported on first use."""

    def __init__(self, *, max_delay: float = 60.0) -> None:
        self._max_delay = max_delay
        self._engine: AutoThrottle | None = None
        self._floors: dict[str, float] = {}

    def _throttle(self) -> AutoThrottle:
        if self._engine is None:
            from scrapling.spiders.throttle import AutoThrottle

            self._engine = AutoThrottle(start_delay=0.0, max_delay=self._max_delay)
        return self._engine

    def delay_for(self, host: str, floor: float) -> float:
        """Seconds to wait before the next request to ``host``, never below ``floor``."""
        self._floors[host] = floor
        return max(floor, self._throttle().delay_for(host, floor))

    def record(self, host: str, *, latency: float, ok: bool, retry_after: float | None) -> float:
        """Record one response and return the host's new delay in seconds."""
        floor = self._floors.get(host, 0.0)
        return self._throttle().record(host, latency, ok, floor=floor, retry_after=retry_after)


def default_throttle() -> CrawlThrottle:
    """The engine's throttle when it can be loaded, else the same arithmetic in the stdlib.

    The engine's spider package needs the browser extra; an HTTP-only install still paces
    adaptively.
    """
    try:
        import scrapling.spiders.throttle  # noqa: F401
    except ImportError:
        from rastro.application.adaptive_throttle import AdaptiveThrottle

        return AdaptiveThrottle()
    return ScraplingThrottle()
