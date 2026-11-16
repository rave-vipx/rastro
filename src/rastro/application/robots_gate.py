"""robots.txt enforcement for one request, with the run's cache (N10, SEC-008, SEC-003)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit

import structlog

from rastro.application.acquisition_state import _Host, _Operation
from rastro.application.http_policy import REDIRECT_STATUSES, check_status, redirect_target
from rastro.application.robots import parse_robots_response
from rastro.application.robots_cache import RobotsEntry, raise_failure
from rastro.domain.acquisition import AcquisitionError, HttpResponse
from rastro.domain.models import Outcome

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
Request = Callable[..., Awaitable[HttpResponse]]


async def check_robots(
    url: str, operation: _Operation, *, request: Request, hosts: Mapping[str, _Host]
) -> None:
    """Raise ``robots_denied`` (or the refused fetch's category) unless ``url`` may be fetched."""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if hostname in operation.policy.grants.ignore_robots:
        # The operator asserts permission (SEC-003); every other check still applies.
        if hostname not in operation.overridden:
            operation.overridden.add(hostname)
            _LOG.info("robots_overridden", host=hostname)
        return
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    async with operation.robots.lock(origin):
        entry = operation.robots.entries.get(origin)
        if entry is None:
            operation.robots_fetch = True
            try:
                entry = await _fetch(origin, operation, request)
            finally:
                operation.robots_fetch = False
            operation.robots.entries[origin] = entry
            if entry.failure is None:
                _pace(hosts[hostname], entry.min_interval, fresh=True)
        elif entry.failure is None:
            _pace(hosts[hostname], entry.min_interval, fresh=False)
    raise_failure(entry)
    if entry.rules is not None and not entry.rules.can_fetch(url, "Rastro"):
        raise AcquisitionError("robots_denied", Outcome.ACCESS_RESTRICTION)


def _pace(host: _Host, min_interval: float, *, fresh: bool) -> None:
    host.interval = max(host.interval, min_interval)
    if fresh:
        host.next_request = max(host.next_request, perf_counter() + host.interval)


async def _fetch(origin: str, operation: _Operation, request: Request) -> RobotsEntry:
    """A refused answer is an entry too; a transport failure is not (it is not the origin's)."""
    robots_url = origin + "/robots.txt"
    for hop in range(operation.policy.max_redirects + 1):
        response = await request(robots_url, operation, max_bytes=512_000)
        try:
            if response.status in REDIRECT_STATUSES:
                if hop == operation.policy.max_redirects:
                    raise AcquisitionError("robots_redirect_limit", Outcome.ACCESS_RESTRICTION)
                robots_url = redirect_target(robots_url, response, operation.policy)
                operation.redirects += 1
                continue
            if response.status in {404, 410}:
                return RobotsEntry()
            check_status(response.status)
            rules, min_interval = parse_robots_response(response)
            return RobotsEntry(rules, min_interval)
        except AcquisitionError as error:
            return RobotsEntry(failure=(error.code, error.outcome))
    raise AssertionError("Redirect loop must return")  # pragma: no cover
