"""Mutable host and operation accounting shared by bounded acquisition attempts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rastro.domain.acquisition import ProxyRoute

if TYPE_CHECKING:
    from protego import Protego


@dataclass
class _Host:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_request: float = 0
    cooldown: float = 0
    denied: bool = False
    interval: float = 0


@dataclass
class _Operation:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    requests: int = 0
    retries: int = 0
    redirects: int = 0
    bytes_received: int = 0
    network_seconds: float = 0
    http_status: int | None = None
    retry_after_seconds: float | None = None
    robots: dict[str, Protego | None] = field(default_factory=dict)
    proxy: ProxyRoute | None = None
