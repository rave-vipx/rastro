"""Per-host pacing and concurrency, resolved from the job-wide values and `hosts` overrides."""

from __future__ import annotations

import asyncio
from time import perf_counter

from rastro.application.acquisition_state import _Host, _Operation
from rastro.application.http_policy import TRANSIENT_STATUSES, retry_after
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, HttpResponse
from rastro.domain.models import Outcome
from rastro.ports.engine import CrawlThrottle

__all__ = ["HostThrottle", "admit", "build_hosts"]


def build_hosts(policy: AcquisitionPolicy) -> dict[str, _Host]:
    """One state per allowed host. A host without an override is spaced by the job-wide
    interval and fetched one transfer at a time; an entry in `hosts` may change both."""
    hosts: dict[str, _Host] = {}
    for name in policy.allowed_hosts:
        limits = policy.hosts.get(name)
        interval = policy.min_interval_seconds
        concurrency = 1
        if limits is not None:
            if limits.min_interval_seconds is not None:
                interval = float(limits.min_interval_seconds)
            if limits.max_concurrency is not None:
                concurrency = limits.max_concurrency
        hosts[name] = _Host(interval=interval, slots=asyncio.Semaphore(concurrency))
    return hosts


async def admit(host: _Host, operation: _Operation, policy: AcquisitionPolicy) -> None:
    """Hold the host's lock only to check its state and wait out its spacing; the caller
    already owns one of its transfer slots and then runs the transfer outside the lock."""
    async with host.lock:
        if host.denied:
            raise AcquisitionError("host_access_restricted", Outcome.ACCESS_RESTRICTION)
        remaining = host.cooldown - perf_counter()
        if remaining > 0:
            operation.retry_after_seconds = remaining
            raise AcquisitionError("retry_after_pending", Outcome.ACCESS_RESTRICTION)
        await asyncio.sleep(max(0, host.next_request - perf_counter()))
        if operation.requests >= policy.max_requests:
            raise AcquisitionError("request_limit", Outcome.PERMANENT_FAILURE)
        operation.rotate()
        host.next_request = perf_counter() + host.interval  # spaces the next start


class HostThrottle:
    """Adaptive spacing for the hosts whose limits turn on ``autothrottle``.

    Before each transfer the host's interval becomes the throttle's delay; after each
    response the throttle sees its latency, whether it was refused (429, 503 and the other
    transient answers) and any ``Retry-After``. The interval never drops below the floor the
    job configured for that host. Other hosts keep their fixed interval.
    """

    def __init__(
        self,
        policy: AcquisitionPolicy,
        hosts: dict[str, _Host],
        throttle: CrawlThrottle | None = None,
    ) -> None:
        self._floors = {
            name: hosts[name].interval
            for name, limits in policy.hosts.items()
            if limits.autothrottle and name in hosts
        }
        self._throttle = throttle
        if self._floors and throttle is None:
            self._throttle = _engine_throttle()

    def pace(self, name: str, host: _Host) -> None:
        """Set the host's interval for its next transfer."""
        floor = self._floors.get(name)
        if floor is not None and self._throttle is not None:
            host.interval = max(floor, self._throttle.delay_for(name, floor))

    def observe(self, name: str, host: _Host, response: HttpResponse, latency: float) -> None:
        """Feed one response back and space the host's next start by the new delay."""
        floor = self._floors.get(name)
        if floor is None or self._throttle is None:
            return
        refused = response.status == 429 or response.status in TRANSIENT_STATUSES
        delay = self._throttle.record(
            name,
            latency=latency,
            ok=not refused,
            retry_after=retry_after(response.headers.get("retry-after")),
        )
        host.interval = max(floor, delay)
        host.next_request = max(host.next_request, perf_counter() + host.interval)


def _engine_throttle() -> CrawlThrottle:
    from rastro.adapters.scrapling_throttle import default_throttle

    return default_throttle()
