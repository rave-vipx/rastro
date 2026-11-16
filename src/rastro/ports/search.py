"""Search provider port for turning research terms into candidate source URLs (DISC-001)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Return up to ``limit`` candidate search hits for ``query``."""
        ...
