"""Bounded public-web acquisition. All failures carry safe, stable categories."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from rastro.application.destinations import permitted_url, pin_destination
from rastro.application.http_policy import (
    REDIRECT_STATUSES,
    TRANSIENT_STATUSES,
    check_status,
    decode_body,
    redirect_target,
    request_headers,
    resolve_proxy,
    retry_after,
)
from rastro.application.robots import parse_robots_response
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    ProxyRoute,
)
from rastro.domain.models import Outcome, Representation, Source
from rastro.ports.acquisition import HttpTransport, Resolver

if TYPE_CHECKING:
    from protego import Protego

__all__ = ["WebAcquirer", "retry_after"]


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


class WebAcquirer:
    """One client per event loop; limits and restriction cooldowns are shared by its jobs."""

    def __init__(
        self,
        policy: AcquisitionPolicy,
        *,
        transport: HttpTransport,
        resolver: Resolver,
        environ: Mapping[str, str] | None = None,
    ):
        self.policy = policy
        self._transport = transport
        self._resolver = resolver
        self._environ = environ
        self._slots = asyncio.Semaphore(policy.max_concurrency)
        self._hosts = {
            host: _Host(interval=policy.min_interval_seconds) for host in policy.allowed_hosts
        }

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        started = perf_counter()
        operation = _Operation(source.url)
        body: str | None = None
        issue: str | None = None
        status = Outcome.SUCCESS
        try:
            operation.proxy = resolve_proxy(source.egress, self.policy, self._environ)
            operation.headers = request_headers(source, representation)
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                url = source.url
                for hop in range(self.policy.max_redirects + 1):
                    url, _, _ = permitted_url(url, self.policy)
                    operation.url = url
                    await self._robots(url, operation)
                    response = await self._request(url, operation, max_bytes=max_bytes)
                    if response.status not in REDIRECT_STATUSES:
                        check_status(response.status)
                        body = decode_body(response, representation)
                        break
                    if hop == self.policy.max_redirects:
                        raise AcquisitionError("redirect_limit", Outcome.PERMANENT_FAILURE)
                    url = redirect_target(url, response, self.policy)
                    operation.redirects += 1
        except TimeoutError:
            status, issue = Outcome.TRANSIENT_FAILURE, "operation_timeout"
        except AcquisitionError as error:
            status, issue = error.outcome, error.code
        return AcquisitionResult(
            url=operation.url,
            body=body,
            status=status,
            issue_code=issue,
            http_status=operation.http_status,
            requests=operation.requests,
            retries=operation.retries,
            redirects=operation.redirects,
            bytes_received=operation.bytes_received,
            network_seconds=operation.network_seconds,
            acquisition_seconds=perf_counter() - started,
            retry_after_seconds=operation.retry_after_seconds,
            egress=source.egress,
        )

    async def _robots(self, url: str, operation: _Operation) -> None:
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if origin not in operation.robots:
            robots_url = origin + "/robots.txt"
            for hop in range(self.policy.max_redirects + 1):
                response = await self._request(robots_url, operation, max_bytes=512_000)
                if response.status in REDIRECT_STATUSES:
                    if hop == self.policy.max_redirects:
                        raise AcquisitionError("robots_redirect_limit", Outcome.ACCESS_RESTRICTION)
                    robots_url = redirect_target(robots_url, response, self.policy)
                    operation.redirects += 1
                    continue
                if response.status in {404, 410}:
                    operation.robots[origin] = None
                    break
                check_status(response.status)
                rules, min_interval = parse_robots_response(response)
                operation.robots[origin] = rules
                host = self._hosts[parsed.hostname or ""]
                host.interval = max(host.interval, min_interval)
                host.next_request = max(host.next_request, perf_counter() + host.interval)
                break
        cached_rules = operation.robots[origin]
        if cached_rules is not None and not cached_rules.can_fetch(url, "Rastro"):
            raise AcquisitionError("robots_denied", Outcome.ACCESS_RESTRICTION)

    async def _request(self, url: str, operation: _Operation, *, max_bytes: int) -> HttpResponse:
        _, hostname, _ = permitted_url(url, self.policy)
        host = self._hosts[hostname]
        for attempt in range(self.policy.max_retries + 1):
            try:
                response = await self._attempt_request(url, host, operation, max_bytes=max_bytes)
                if response.status not in TRANSIENT_STATUSES or attempt == self.policy.max_retries:
                    return response
            except (TimeoutError, AcquisitionError) as error:
                if isinstance(error, TimeoutError):
                    error = AcquisitionError("request_timeout", Outcome.TRANSIENT_FAILURE)
                if (
                    error.outcome != Outcome.TRANSIENT_FAILURE
                    or error.code == "retry_after_pending"
                    or attempt == self.policy.max_retries
                ):
                    raise error from None
            operation.retries += 1
            await asyncio.sleep(
                min(self.policy.backoff_seconds * 2**attempt, self.policy.max_retry_delay_seconds)
            )
        raise AssertionError("Retry loop must return or raise")  # pragma: no cover

    async def _attempt_request(
        self, url: str, host: _Host, operation: _Operation, *, max_bytes: int
    ) -> HttpResponse:
        # Waiting jobs for one host cannot occupy all global transfer slots.
        async with host.lock:
            if host.denied:
                raise AcquisitionError("host_access_restricted", Outcome.ACCESS_RESTRICTION)
            remaining = host.cooldown - perf_counter()
            if remaining > 0:
                operation.retry_after_seconds = remaining
                raise AcquisitionError("retry_after_pending", Outcome.ACCESS_RESTRICTION)
            await asyncio.sleep(max(0, host.next_request - perf_counter()))
            if operation.requests >= self.policy.max_requests:
                raise AcquisitionError("request_limit", Outcome.PERMANENT_FAILURE)
            response = await self._pinned_get(url, host, operation, max_bytes=max_bytes)
            operation.bytes_received += response.bytes_received
            operation.http_status = response.status
            if response.status in {401, 403, 451}:
                host.denied = True
            if response.status == 429:
                delay = retry_after(response.headers.get("retry-after"))
                if delay is None:
                    delay = self.policy.max_retry_delay_seconds
                host.cooldown = perf_counter() + delay
                operation.retry_after_seconds = delay
                raise AcquisitionError("rate_limited", Outcome.ACCESS_RESTRICTION)
            if response.status in TRANSIENT_STATUSES:
                delay = retry_after(response.headers.get("retry-after"))
                if "retry-after" in response.headers and delay is None:
                    delay = self.policy.max_retry_delay_seconds
                if delay is not None:
                    host.cooldown = perf_counter() + delay
                    operation.retry_after_seconds = delay
                    # Defer to the caller; never retry before a server's deadline.
                    raise AcquisitionError("retry_after_pending", Outcome.TRANSIENT_FAILURE)
            return response

    async def _pinned_get(
        self, url: str, host: _Host, operation: _Operation, *, max_bytes: int
    ) -> HttpResponse:
        async with self._slots:
            operation.requests += 1
            try:
                async with asyncio.timeout(self.policy.request_timeout_seconds):
                    target = await pin_destination(url, self.policy, self._resolver)
                    network_start = perf_counter()
                    try:
                        return await self._transport.get(
                            target,
                            headers=operation.headers,
                            timeout=self.policy.request_timeout_seconds,
                            max_bytes=max_bytes,
                            proxy=operation.proxy,
                        )
                    finally:
                        operation.network_seconds += perf_counter() - network_start
            finally:
                host.next_request = perf_counter() + host.interval
