"""One run's robots.txt answers, in memory only (N10, SEC-008).

A cache lives for one run or one registry install, entered with :func:`robots_cache`.
It is never global across runs and never persisted. Outside a scope, each acquisition
keeps a private cache, which is the behaviour before this module existed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rastro.domain.acquisition import AcquisitionError
from rastro.domain.models import Outcome

if TYPE_CHECKING:
    from protego import Protego


@dataclass(frozen=True)
class RobotsEntry:
    """What one origin's robots.txt said: rules (None when absent) or a refusal category."""

    rules: Protego | None = None
    min_interval: float = 0
    failure: tuple[str, Outcome] | None = None  # (code, outcome) of a refused fetch


@dataclass
class RobotsCache:
    entries: dict[str, RobotsEntry] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def lock(self, origin: str) -> asyncio.Lock:
        """One fetch per origin even when several sources reach it at once."""
        return self.locks.setdefault(origin, asyncio.Lock())


_SCOPED: ContextVar[RobotsCache | None] = ContextVar("robots_cache", default=None)


def current_robots_cache() -> RobotsCache:
    """The scoped cache, or a private one for a single acquisition."""
    return _SCOPED.get() or RobotsCache()


@contextmanager
def robots_cache() -> Iterator[RobotsCache]:
    """Share robots answers inside the block; a nested block reuses the outer cache."""
    existing = _SCOPED.get()
    if existing is not None:
        yield existing
        return
    cache = RobotsCache()
    token = _SCOPED.set(cache)
    try:
        yield cache
    finally:
        _SCOPED.reset(token)


def raise_failure(entry: RobotsEntry) -> None:
    if entry.failure is not None:
        raise AcquisitionError(*entry.failure)
