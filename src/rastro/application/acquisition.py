"""Bounded public-web acquisition. All failures carry safe, stable categories."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from time import perf_counter
from urllib.parse import urlsplit

from rastro.application.acquisition_state import _Host, _Operation, check_page_request
from rastro.application.auth_headers import request_auth, resolve_auth
from rastro.application.conditional import ConditionalRequests
from rastro.application.cookie_jar import CookieJar
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
from rastro.application.operator_trust import current_trust
from rastro.application.robots_gate import check_robots
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
)
from rastro.domain.models import Outcome, Representation, Source
from rastro.ports.acquisition import HttpTransport, Resolver

__all__ = ["WebAcquirer", "retry_after"]


class WebAcquirer:
    """One client per event loop; limits and restriction cooldowns are shared by its jobs."""

    def __init__(
        self,
        policy: AcquisitionPolicy,
        *,
        transport: HttpTransport,
        resolver: Resolver,
        environ: Mapping[str, str] | None = None,
        conditional: ConditionalRequests | None = None,
    ):
        self.policy = policy
        self._transport = transport
        self._resolver = resolver
        self._conditional = conditional
        self._environ = environ
        self._cookies = CookieJar() if policy.session else None
        self._slots = asyncio.Semaphore(policy.max_concurrency)
        self._hosts = {
            host: _Host(interval=policy.min_interval_seconds) for host in policy.allowed_hosts
        }

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        return await self._acquire(source, max_bytes=max_bytes, representation=representation)

    async def acquire_raw(self, source: Source, *, max_bytes: int) -> AcquisitionResult:
        """Same policy, redirect and robots rules as ``acquire``; the bytes come back undecoded
        in ``raw_body`` for callers that verify them by digest (the registry fetch)."""
        return await self._acquire(source, max_bytes=max_bytes, representation="json", raw=True)

    async def _acquire(
        self, source: Source, *, max_bytes: int, representation: Representation, raw: bool = False
    ) -> AcquisitionResult:
        started = perf_counter()
        policy = self.policy.with_grants(current_trust().network_grants())
        operation = _Operation(source.url, policy, cookie_host=urlsplit(source.url).hostname)
        body: str | None = None
        raw_body: bytes | None = None
        issue: str | None = None
        status = Outcome.SUCCESS
        try:
            operation.proxy = resolve_proxy(source.egress, self.policy, self._environ)
            operation.auth = resolve_auth(self.policy, self._environ)
            operation.headers = request_headers(source, representation)
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                url = source.url
                for hop in range(self.policy.max_redirects + 1):
                    url, _, _ = permitted_url(url, policy)
                    operation.url = url
                    operation.headers = request_headers(source, representation)
                    await self._robots(url, operation)
                    conditional = (
                        await self._conditional.headers(source.url, url)
                        if self._conditional is not None
                        else {}
                    )
                    operation.headers.update(conditional)
                    response = await self._request(url, operation, max_bytes=max_bytes)
                    if response.status == 304 and conditional:
                        status = Outcome.UNCHANGED
                        break
                    if response.status not in REDIRECT_STATUSES:
                        check_status(response.status)
                        if raw:
                            raw_body = response.body
                        else:
                            body = decode_body(response, representation)
                        if self._conditional is not None:
                            self._conditional.remember(source.url, url, response)
                        break
                    if hop == self.policy.max_redirects:
                        raise AcquisitionError("redirect_limit", Outcome.PERMANENT_FAILURE)
                    url = redirect_target(url, response, policy)
                    if urlsplit(url).hostname != operation.cookie_host:
                        operation.cookie_host = None
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
            robots_overridden=bool(operation.overridden),
            raw_body=raw_body,
        )

    async def _robots(self, url: str, operation: _Operation) -> None:
        await check_robots(url, operation, request=self._request, hosts=self._hosts)

    async def _request(self, url: str, operation: _Operation, *, max_bytes: int) -> HttpResponse:
        _, hostname, _ = permitted_url(url, operation.policy)
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

    def _request_headers(
        self, url: str, target: PinnedTarget, address: str, operation: _Operation
    ) -> dict[str, str]:
        headers = dict(operation.headers)
        if self._cookies is not None and target.host == operation.cookie_host:
            cookie = self._cookies.header(url)
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

    async def _pinned_get(
        self, url: str, host: _Host, operation: _Operation, *, max_bytes: int
    ) -> HttpResponse:
        async with self._slots:
            check_page_request(url)
            operation.requests += 1
            deadline = asyncio.get_running_loop().time() + self.policy.request_timeout_seconds
            try:
                async with asyncio.timeout_at(deadline):
                    target = await pin_destination(url, operation.policy, self._resolver)
                    addresses = (target.address, *target.alternates[:2])
                    if operation.proxy is not None:
                        addresses = addresses[:1]
                    for index, address in enumerate(addresses):
                        if index:
                            await asyncio.sleep(host.interval)
                            if operation.requests >= self.policy.max_requests:
                                raise AcquisitionError("request_limit", Outcome.PERMANENT_FAILURE)
                            check_page_request(url)
                            operation.requests += 1
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise AcquisitionError("request_timeout", Outcome.TRANSIENT_FAILURE)
                        # Reserve pacing before dividing the transfer budget among addresses.
                        timeout = max(
                            0.001, remaining - host.interval * (len(addresses) - index - 1)
                        )
                        network_start = perf_counter()
                        try:
                            headers = self._request_headers(url, target, address, operation)
                            response = await self._transport.get(
                                replace(target, address=address, alternates=addresses[index + 1 :]),
                                headers=headers,
                                timeout=timeout,
                                max_bytes=max_bytes,
                                proxy=operation.proxy,
                            )
                            if self._cookies is not None:
                                self._cookies.receive(url, response.headers.get("set-cookie", ""))
                            return response
                        except AcquisitionError as error:
                            if (
                                not error.connect_failure
                                or error.outcome != Outcome.TRANSIENT_FAILURE
                                or index == len(addresses) - 1
                            ):
                                raise
                        finally:
                            operation.network_seconds += perf_counter() - network_start
            finally:
                host.next_request = perf_counter() + host.interval
        raise AssertionError("Address loop must return or raise")  # pragma: no cover
