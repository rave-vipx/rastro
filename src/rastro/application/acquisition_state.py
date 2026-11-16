"""Mutable host and operation accounting shared by bounded acquisition attempts."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, ProxyRoute
from rastro.domain.models import Outcome

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
    policy: AcquisitionPolicy  # the job's policy plus the operator's grants for this operation
    cookie_host: str | None = None
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
    auth: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)
    robots_fetch: bool = False  # robots.txt is fetched without credentials
    overridden: set[str] = field(default_factory=set)


@dataclass
class PaginationScope:
    host: str | None
    secure: bool
    remaining: int
    hosts: frozenset[str] = frozenset()  # further hosts a spider may reach (all allowed_hosts)
    documents: int | None = None  # documents left for the source; None = the recipe's own cap


PAGINATION_SCOPE: ContextVar[PaginationScope | None] = ContextVar("pagination_scope", default=None)


def check_page_request(url: str) -> None:
    """Enforce the traversal's host and transfer budget, including redirects and robots."""
    scope = PAGINATION_SCOPE.get()
    if scope is None:
        return
    reason = pagination_boundary(url, scope.host, scope.secure, scope.hosts)
    if reason:
        raise AcquisitionError(reason, Outcome.PERMANENT_FAILURE)
    if scope.remaining <= 0:
        raise AcquisitionError("request_limit", Outcome.PERMANENT_FAILURE)
    scope.remaining -= 1
    scope.secure |= urlsplit(url).scheme == "https"


def _host(name: str | None) -> str | None:
    """Compare hosts case-insensitively, treating one trailing root dot as the same name."""
    return name.lower().removesuffix(".") if name else name


def pagination_boundary(
    url: str, host: str | None, secure: bool, hosts: frozenset[str] = frozenset()
) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return "invalid_next"
        if _host(parts.hostname) != _host(host) and _host(parts.hostname) not in hosts:
            return "off_host"
        if secure and parts.scheme == "http":
            return "https_downgrade"
    except ValueError:
        return "invalid_next"
    return None
