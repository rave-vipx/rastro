"""Per-host pacing and concurrency, resolved from the job-wide values and `hosts` overrides."""

from __future__ import annotations

import asyncio
from time import perf_counter

from rastro.application.acquisition_state import _Host, _Operation
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.models import Outcome

__all__ = ["admit", "build_hosts"]


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
