"""`hosts.<name>.autothrottle` adapts that host's spacing from latency and refusals."""

import asyncio
import sys
import time

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse
from rastro.domain.browser_controls import HostLimits
from rastro.domain.models import ConfigurationError, Source


class Resolver:
    async def resolve(self, host, port):
        return ("8.8.8.8",)


class Transport:
    def __init__(self, statuses=(), delay=0.0):
        self.statuses = list(statuses)
        self.delay = delay
        self.starts: list[float] = []

    async def get(self, target, *, headers, **kwargs):
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        self.starts.append(time.perf_counter())
        await asyncio.sleep(self.delay)
        status, headers = self.statuses.pop(0) if self.statuses else (200, {})
        return HttpResponse(status, {"content-type": "text/html", **headers}, b"<p>x</p>", 8)


class Throttle:
    """A recording stand-in for the engine's adaptive throttle."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.asked: list[tuple[str, float]] = []
        self.recorded: list[tuple[str, bool, float | None]] = []

    def delay_for(self, host, floor):
        self.asked.append((host, floor))
        return max(floor, self.delay)

    def record(self, host, *, latency, ok, retry_after):
        assert latency >= 0
        self.recorded.append((host, ok, retry_after))
        return max(0.0, self.delay)


def _acquirer(transport, throttle=None, **limits):
    policy = AcquisitionPolicy(
        allowed_hosts=("a.example", "b.example"),
        min_interval_seconds=0,
        max_retries=0,
        max_concurrency=4,
        hosts={"a.example": HostLimits(autothrottle=True, **limits)},
    )
    return WebAcquirer(policy, transport=transport, resolver=Resolver(), throttle=throttle)


@pytest.mark.asyncio
async def test_autothrottled_host_reports_each_response():
    throttle = Throttle()
    transport = Transport(statuses=[(200, {}), (429, {"retry-after": "0"})])
    client = _acquirer(transport, throttle, min_interval_seconds=0.01)
    await client.acquire(Source(url="https://a.example/1"), max_bytes=1000)
    limited = await client.acquire(Source(url="https://a.example/2"), max_bytes=1000)
    assert limited.issue_code == "rate_limited"
    assert ("a.example", 0.01) in throttle.asked
    pages = [entry for entry in throttle.recorded if entry[0] == "a.example"]
    assert pages[-2:] == [("a.example", True, None), ("a.example", False, 0.0)]


@pytest.mark.asyncio
async def test_unavailable_answer_counts_as_a_refusal():
    throttle = Throttle()
    client = _acquirer(Transport(statuses=[(503, {"retry-after": "1"})]), throttle)
    await client.acquire(Source(url="https://a.example/1"), max_bytes=1000)
    assert ("a.example", False, 1.0) in throttle.recorded


@pytest.mark.asyncio
async def test_widened_delay_spaces_the_next_request():
    throttle = Throttle(delay=0.25)
    transport = Transport()
    client = _acquirer(transport, throttle)
    for index in range(2):
        await client.acquire(Source(url=f"https://a.example/{index}"), max_bytes=1000)
    assert transport.starts[1] - transport.starts[0] >= 0.25


@pytest.mark.asyncio
async def test_the_delay_never_drops_below_the_configured_interval():
    throttle = Throttle(delay=0.0)
    transport = Transport()
    client = _acquirer(transport, throttle, min_interval_seconds=0.2)
    for index in range(2):
        await client.acquire(Source(url=f"https://a.example/{index}"), max_bytes=1000)
    assert transport.starts[1] - transport.starts[0] >= 0.2


@pytest.mark.asyncio
async def test_hosts_without_autothrottle_keep_their_fixed_interval():
    throttle = Throttle(delay=5.0)
    transport = Transport()
    client = _acquirer(transport, throttle)
    started = time.perf_counter()
    for index in range(2):
        await client.acquire(Source(url=f"https://b.example/{index}"), max_bytes=1000)
    assert time.perf_counter() - started < 2
    assert all(entry[0] != "b.example" for entry in throttle.asked + throttle.recorded)


@pytest.mark.asyncio
async def test_slow_host_widens_its_spacing_with_the_engine_throttle():
    transport = Transport(delay=0.15)
    client = _acquirer(transport)  # the engine-backed throttle
    for index in range(3):
        await client.acquire(Source(url=f"https://a.example/{index}"), max_bytes=1000)
    # Each gap is the transfer plus a delay that follows the observed latency.
    assert transport.starts[2] - transport.starts[1] >= 0.25


def test_autothrottle_without_the_engine_fails_loudly(monkeypatch):
    monkeypatch.setitem(sys.modules, "rastro.adapters.scrapling_throttle", None)
    with pytest.raises(ConfigurationError, match="autothrottle"):
        _acquirer(Transport())


def test_no_engine_needed_when_no_host_asks_for_autothrottle(monkeypatch):
    monkeypatch.setitem(sys.modules, "rastro.adapters.scrapling_throttle", None)
    policy = AcquisitionPolicy(allowed_hosts=("a.example",))
    WebAcquirer(policy, transport=Transport(), resolver=Resolver())
