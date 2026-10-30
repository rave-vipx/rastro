"""Bounded public-web acquisition. All failures carry safe, stable categories."""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from time import perf_counter
from urllib.parse import urljoin, urlsplit, urlunsplit

from protego import Protego

from rastro.application.destinations import permitted_url, pin_destination
from rastro.application.robots import parse_robots
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
)
from rastro.domain.models import LANGUAGE_TAG, Outcome, Source
from rastro.ports.acquisition import HttpTransport, Resolver

_USER_AGENT = "Rastro/0.1 (+https://github.com/rave-vipx/rastro)"
_REDIRECTS = {301, 302, 303, 307, 308}
_TRANSIENT = {408, 425, 500, 502, 503, 504}


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
    headers: dict[str, str]
    requests: int = 0
    retries: int = 0
    redirects: int = 0
    bytes_received: int = 0
    network_seconds: float = 0
    http_status: int | None = None
    retry_after_seconds: float | None = None
    robots: dict[str, Protego | None] = field(default_factory=dict)


def retry_after(value: str | None) -> float | None:
    """Parse delta seconds or an HTTP date without shortening a server's delay."""
    if value is None:
        return None
    if re.fullmatch(r"[0-9]+", value.strip()):
        return min(float(value), sys.float_info.max)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def _decode(response: HttpResponse) -> str:
    media = Message()
    media["content-type"] = response.headers.get("content-type", "")
    if media.get_content_type() not in {"text/html", "application/xhtml+xml"}:
        raise AcquisitionError("unsupported_content_type", Outcome.PERMANENT_FAILURE)
    try:
        return response.body.decode(media.get_content_charset() or "utf-8", errors="strict")
    except (LookupError, UnicodeError):
        raise AcquisitionError("invalid_encoding", Outcome.INVALID_DATA) from None


class WebAcquirer:
    """One client per event loop; limits and restriction cooldowns are shared by its jobs."""

    def __init__(self, policy: AcquisitionPolicy, *, transport: HttpTransport, resolver: Resolver):
        self.policy = policy
        self._transport = transport
        self._resolver = resolver
        self._slots = asyncio.Semaphore(policy.max_concurrency)
        self._hosts = {
            host: _Host(interval=policy.min_interval_seconds) for host in policy.allowed_hosts
        }

    async def acquire(self, source: Source, *, max_bytes: int) -> AcquisitionResult:
        started = perf_counter()
        operation = _Operation(source.url, {"User-Agent": _USER_AGENT, "Accept": "text/html"})
        html: str | None = None
        issue: str | None = None
        status = Outcome.SUCCESS
        try:
            if source.egress != "direct":
                raise AcquisitionError("unsupported_egress", Outcome.PERMANENT_FAILURE)
            languages = source.languages or (() if source.locale == "und" else (source.locale,))
            if len(languages) > 20 or any(
                re.fullmatch(LANGUAGE_TAG, language) is None for language in languages
            ):
                raise AcquisitionError("invalid_locale", Outcome.PERMANENT_FAILURE)
            if languages:
                operation.headers["Accept-Language"] = ", ".join(
                    language if index == 0 else f"{language};q={(20 - index) / 20:g}"
                    for index, language in enumerate(languages)
                )
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                url = source.url
                for hop in range(self.policy.max_redirects + 1):
                    url, _, _ = permitted_url(url, self.policy)
                    operation.url = url
                    await self._robots(url, operation)
                    response = await self._request(url, operation, max_bytes=max_bytes)
                    if response.status not in _REDIRECTS:
                        self._check_status(response.status)
                        html = _decode(response)
                        break
                    if hop == self.policy.max_redirects:
                        raise AcquisitionError("redirect_limit", Outcome.PERMANENT_FAILURE)
                    url = self._redirect(url, response)
                    operation.redirects += 1
        except TimeoutError:
            status, issue = Outcome.TRANSIENT_FAILURE, "operation_timeout"
        except AcquisitionError as error:
            status, issue = error.outcome, error.code
        return AcquisitionResult(
            url=operation.url,
            html=html,
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
        )

    def _redirect(self, url: str, response: HttpResponse) -> str:
        location = response.headers.get("location")
        if not location:
            raise AcquisitionError("invalid_redirect", Outcome.PERMANENT_FAILURE)
        try:
            joined = urljoin(url, location)
        except ValueError:
            raise AcquisitionError("invalid_redirect", Outcome.PERMANENT_FAILURE) from None
        target, _, _ = permitted_url(joined, self.policy)
        if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
            raise AcquisitionError("https_downgrade", Outcome.ACCESS_RESTRICTION)
        return target

    @staticmethod
    def _check_status(status: int) -> None:
        if status == 200:
            return
        if status in {401, 403, 429, 451}:
            raise AcquisitionError("access_restricted", Outcome.ACCESS_RESTRICTION)
        if status in _TRANSIENT or status >= 500:
            raise AcquisitionError("http_transient_failure", Outcome.TRANSIENT_FAILURE)
        raise AcquisitionError("http_permanent_failure", Outcome.PERMANENT_FAILURE)

    async def _robots(self, url: str, operation: _Operation) -> None:
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if origin not in operation.robots:
            robots_url = origin + "/robots.txt"
            for hop in range(self.policy.max_redirects + 1):
                response = await self._request(robots_url, operation, max_bytes=512_000)
                if response.status in _REDIRECTS:
                    if hop == self.policy.max_redirects:
                        raise AcquisitionError("robots_redirect_limit", Outcome.ACCESS_RESTRICTION)
                    robots_url = self._redirect(robots_url, response)
                    operation.redirects += 1
                    continue
                if response.status in {404, 410}:
                    operation.robots[origin] = None
                    break
                self._check_status(response.status)
                if "html" in response.headers.get("content-type", "").lower():
                    raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION)
                rules = parse_robots(response.body)
                operation.robots[origin] = rules
                host = self._hosts[parsed.hostname or ""]
                delay = rules.crawl_delay("Rastro") or 0
                rate = rules.request_rate("Rastro")
                # Conservatively space requests by the whole window, avoiding bursts.
                host.interval = max(host.interval, delay, rate.seconds if rate else 0)
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
                    async with self._slots:
                        operation.requests += 1
                        try:
                            async with asyncio.timeout(self.policy.request_timeout_seconds):
                                target = await pin_destination(url, self.policy, self._resolver)
                                network_start = perf_counter()
                                try:
                                    response = await self._transport.get(
                                        target,
                                        headers=operation.headers,
                                        timeout=self.policy.request_timeout_seconds,
                                        max_bytes=max_bytes,
                                    )
                                finally:
                                    operation.network_seconds += perf_counter() - network_start
                        finally:
                            host.next_request = perf_counter() + host.interval
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
                    if response.status not in _TRANSIENT:
                        return response
                    delay = retry_after(response.headers.get("retry-after"))
                    if "retry-after" in response.headers and delay is None:
                        delay = self.policy.max_retry_delay_seconds
                    if delay is not None:
                        host.cooldown = perf_counter() + delay
                        operation.retry_after_seconds = delay
                        # Defer to the caller; never retry before a server's deadline.
                        raise AcquisitionError("retry_after_pending", Outcome.TRANSIENT_FAILURE)
                    if attempt == self.policy.max_retries:
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
