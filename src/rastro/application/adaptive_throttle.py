"""A standard-library adaptive per-host delay, for installs without the crawl engine.

The arithmetic matches the engine's throttle (ADR 0040): the delay starts at the host's floor,
moves halfway towards the last response latency, never drops on a refusal (it doubles, or
waits what ``Retry-After`` asks), never falls below the floor and never exceeds ``max_delay``.
"""

from __future__ import annotations

__all__ = ["AdaptiveThrottle"]

BACKOFF = 2.0


class AdaptiveThrottle:
    """``CrawlThrottle`` with no third-party dependency."""

    def __init__(self, *, max_delay: float = 60.0) -> None:
        self._max_delay = max_delay
        self._delays: dict[str, float] = {}
        self._floors: dict[str, float] = {}

    def delay_for(self, host: str, floor: float) -> float:
        """Seconds to wait before the next request to ``host``, never below ``floor``."""
        self._floors[host] = floor
        if host not in self._delays:
            self._delays[host] = min(max(floor, 0.0), self._max_delay)
        return max(floor, self._delays[host])

    def record(self, host: str, *, latency: float, ok: bool, retry_after: float | None) -> float:
        """Record one response and return the host's new delay in seconds."""
        floor = self._floors.get(host, 0.0)
        current = self.delay_for(host, floor)
        target = max(latency, 0.0)
        delay = max((current + target) / 2, target)
        if not ok:
            penalty = retry_after if retry_after is not None else current * BACKOFF
            delay = max(delay, penalty, current)
        self._delays[host] = min(max(delay, floor), self._max_delay)
        return self._delays[host]
