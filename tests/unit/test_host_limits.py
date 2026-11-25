"""Per-host spacing and concurrency are enforced for every fetch of one acquirer."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from rastro.application.acquisition import WebAcquirer
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse
from rastro.domain.browser_controls import HostLimits
from rastro.domain.models import Source

# Timers on some platforms (Windows) tick every ~15 ms; intervals are checked with that slack.
CLOCK_SLACK = 0.02


class Resolver:
    async def resolve(self, host, port):
        return ("8.8.8.8",)


class Transport:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.starts: list[float] = []

    async def get(self, target, *, headers, **kwargs):
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        self.starts.append(time.perf_counter())
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        return HttpResponse(200, {"content-type": "text/html"}, b"<p>x</p>", 8)


def _acquirer(transport, **policy):
    base = {"allowed_hosts": ("a.example", "b.example"), "max_concurrency": 8}
    return WebAcquirer(AcquisitionPolicy(**base | policy), transport=transport, resolver=Resolver())


async def _fetch_all(client, urls):
    return await asyncio.gather(*(client.acquire(Source(url=u), max_bytes=1000) for u in urls))


@pytest.mark.asyncio
async def test_host_interval_overrides_global():
    transport = Transport()
    client = _acquirer(
        transport,
        min_interval_seconds=30,
        hosts={"a.example": HostLimits(min_interval_seconds=0.15)},
    )
    started = time.perf_counter()
    await _fetch_all(client, ["https://a.example/1", "https://a.example/2"])
    gap = transport.starts[1] - transport.starts[0]
    assert 0.15 - CLOCK_SLACK <= gap < 5
    assert time.perf_counter() - started < 5


@pytest.mark.asyncio
async def test_host_without_entry_keeps_global_interval():
    transport = Transport()
    client = _acquirer(
        transport,
        min_interval_seconds=0.15,
        hosts={"a.example": HostLimits(min_interval_seconds=0)},
    )
    await _fetch_all(client, ["https://b.example/1", "https://b.example/2"])
    assert transport.starts[1] - transport.starts[0] >= 0.15 - CLOCK_SLACK


@pytest.mark.asyncio
async def test_host_concurrency_shared_by_spider_and_detail():
    transport = Transport(delay=0.1)
    client = _acquirer(
        transport,
        min_interval_seconds=0,
        hosts={"a.example": HostLimits(max_concurrency=2)},
    )
    # Listing pages and detail pages of one run share the client, so they share the cap.
    pages = [f"https://a.example/list/{i}" for i in range(3)]
    details = [f"https://a.example/item/{i}" for i in range(3)]
    results = await _fetch_all(client, pages + details)
    assert all(result.status.value == "success" for result in results)
    assert transport.peak == 2


@pytest.mark.asyncio
async def test_unset_host_concurrency_stays_one_per_host():
    transport = Transport(delay=0.05)
    client = _acquirer(transport, min_interval_seconds=0)
    await _fetch_all(client, [f"https://a.example/{i}" for i in range(3)])
    assert transport.peak == 1


@pytest.mark.asyncio
async def test_concurrency_is_per_host():
    transport = Transport(delay=0.1)
    client = _acquirer(transport, min_interval_seconds=0)
    await _fetch_all(client, ["https://a.example/1", "https://b.example/1"])
    assert transport.peak == 2


def test_unknown_host_in_hosts_refused():
    with pytest.raises(ValidationError, match="allowed host"):
        AcquisitionPolicy(
            allowed_hosts=("a.example",), hosts={"other.example": HostLimits(max_concurrency=2)}
        )
