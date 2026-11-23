"""Mutable host and operation accounting shared by bounded acquisition attempts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from urllib.parse import urlsplit

from rastro.application.auth_headers import request_auth
from rastro.application.blocked_hint import blocked_hint, response_marker
from rastro.application.cookie_jar import CookieJar
from rastro.application.http_policy import TRANSIENT_STATUSES, retry_after
from rastro.application.robots_cache import RobotsCache, current_robots_cache
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
    ProxyRoute,
)
from rastro.domain.models import Outcome
from rastro.domain.source_notes import BlockKind, HttpVersion


@dataclass
class _Host:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_request: float = 0
    cooldown: float = 0
    denied: bool = False
    interval: float = 0
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))


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
    robots: RobotsCache = field(default_factory=current_robots_cache)
    proxy: ProxyRoute | None = None
    auth: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)
    robots_fetch: bool = False  # robots.txt is fetched without credentials
    overridden: set[str] = field(default_factory=set)
    rotation: Callable[[], ProxyRoute] | None = None  # set when the source rotates proxies
    proxies_used: set[str] = field(default_factory=set)
    block_marker: BlockKind | None = None  # what the last refusal looked like
    challenge_solved: bool = False  # a browser cleared a challenge on the way to the page
    http_version: HttpVersion | None = None  # what the server last answered with
    fallbacks: set[str] = field(default_factory=set)  # transfer options not honoured

    def rotate(self) -> None:
        """Route the next transfer through the next rotated profile, if the source rotates."""
        if self.rotation is not None:
            self.proxy = self.rotation()
            self.proxies_used.add(self.proxy.name)

    def result(
        self,
        *,
        body: str | None,
        raw_body: bytes | None,
        status: Outcome,
        issue: str | None,
        egress: str,
        started: float,
    ) -> AcquisitionResult:
        return AcquisitionResult(
            url=self.url,
            body=body,
            status=status,
            issue_code=issue,
            http_status=self.http_status,
            requests=self.requests,
            retries=self.retries,
            redirects=self.redirects,
            bytes_received=self.bytes_received,
            network_seconds=self.network_seconds,
            acquisition_seconds=perf_counter() - started,
            retry_after_seconds=self.retry_after_seconds,
            egress=egress,
            robots_overridden=bool(self.overridden),
            proxy_rotation=self.rotation is not None,
            proxies_used=len(self.proxies_used),
            challenge_solved=self.challenge_solved,
            http_version=self.http_version,
            transfer_issues=tuple(sorted(self.fallbacks)),
            blocked=blocked_hint(issue, self.http_status, self.block_marker),
            raw_body=raw_body,
        )


def record_response(
    response: HttpResponse, host: _Host, operation: _Operation, policy: AcquisitionPolicy
) -> None:
    """Account for one response and apply the host's denial, rate-limit and retry-after state."""
    operation.bytes_received += response.bytes_received
    operation.http_status = response.status
    operation.http_version = response.http_version or operation.http_version
    operation.fallbacks.update(response.fallbacks)
    operation.block_marker = response_marker(response)
    if response.status in {401, 403, 451}:
        host.denied = True
    if response.status == 429:
        delay = retry_after(response.headers.get("retry-after"))
        if delay is None:
            delay = policy.max_retry_delay_seconds
        host.cooldown = perf_counter() + delay
        operation.retry_after_seconds = delay
        raise AcquisitionError("rate_limited", Outcome.ACCESS_RESTRICTION)
    if response.status in TRANSIENT_STATUSES:
        delay = retry_after(response.headers.get("retry-after"))
        if "retry-after" in response.headers and delay is None:
            delay = policy.max_retry_delay_seconds
        if delay is not None:
            host.cooldown = perf_counter() + delay
            operation.retry_after_seconds = delay
            # Defer to the caller; never retry before a server's deadline.
            raise AcquisitionError("retry_after_pending", Outcome.TRANSIENT_FAILURE)


def transfer_headers(
    cookies: CookieJar | None, url: str, target: PinnedTarget, address: str, operation: _Operation
) -> dict[str, str]:
    """Headers for one transfer: the host's session cookie and auth (never auth for robots)."""
    headers = dict(operation.headers)
    if cookies is not None and target.host == operation.cookie_host:
        cookie = cookies.header(url)
        if cookie:
            headers["Cookie"] = cookie
    if not operation.robots_fetch:
        headers.update(
            request_auth(
                operation.auth,
                url,
                address,
                proxied=operation.proxy is not None,
                grants=operation.policy.grants,
            )
        )
    return headers


@dataclass
class PaginationScope:
    host: str | None
    secure: bool
    remaining: int
    total: int = 0  # the budget this traversal started with
    hosts: frozenset[str] = frozenset()  # further hosts a spider may reach (all allowed_hosts)


PAGINATION_SCOPE: ContextVar[PaginationScope | None] = ContextVar("pagination_scope", default=None)
# One counter per spider page fetch: what ``check_page_request`` charged on that page's behalf.
PAGE_CHARGES: ContextVar[list[int] | None] = ContextVar("page_charges", default=None)


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
    if (charges := PAGE_CHARGES.get()) is not None:
        charges[0] += 1
    scope.secure |= urlsplit(url).scheme == "https"


def charge_extra_requests(count: int) -> None:
    """Charge transfers a browser made on a page's behalf beyond the one already charged."""
    scope = PAGINATION_SCOPE.get()
    if scope is None or count <= 0:
        return
    scope.remaining -= count
    if (charges := PAGE_CHARGES.get()) is not None:
        charges[0] += count


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
