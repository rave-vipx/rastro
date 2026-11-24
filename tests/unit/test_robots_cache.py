"""One robots.txt answer per origin and run, never across runs."""

import asyncio

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.operator_trust import operator_trust
from rastro.application.robots_cache import robots_cache
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse
from rastro.domain.models import Outcome, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

HOST = "docs.example"
POLICY = AcquisitionPolicy(
    allowed_hosts=(HOST,), min_interval_seconds=0, max_retries=0, max_requests=50
)


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    def __init__(self, robots):
        self.robots = robots
        self.urls = []

    async def get(self, target, *, headers, timeout, max_bytes, proxy):
        self.urls.append(target.url)
        if target.url.endswith("/robots.txt"):
            return self.robots
        return HttpResponse(200, {"content-type": "text/html"}, b"<p>ok</p>", 9)

    @property
    def robots_requests(self):
        return sum(url.endswith("/robots.txt") for url in self.urls)


def run_async(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


def acquirer(transport):
    return WebAcquirer(POLICY, transport=transport, resolver=Resolver())


def get(web, path):
    return web.acquire(Source(url=f"https://{HOST}{path}"), max_bytes=1000)


def not_found():
    return HttpResponse(404, {}, b"", 0)


def test_without_a_scope_every_acquisition_asks_again():
    transport = Transport(not_found())
    web = acquirer(transport)

    async def run():
        for path in ("/a", "/b"):
            assert (await get(web, path)).status == Outcome.SUCCESS

    run_async(run())
    assert transport.robots_requests == 2


def test_a_scope_asks_once_and_a_new_scope_asks_again():
    transport = Transport(not_found())
    web = acquirer(transport)

    async def run():
        for path in ("/a", "/b", "/c"):
            assert (await get(web, path)).status == Outcome.SUCCESS

    for _ in range(2):
        with robots_cache():
            run_async(run())
    assert transport.robots_requests == 2


def test_a_cached_deny_still_denies_and_does_not_refetch():
    body = b"User-agent: *\nDisallow: /private\n"
    transport = Transport(HttpResponse(200, {"content-type": "text/plain"}, body, len(body)))
    web = acquirer(transport)

    async def run():
        assert (await get(web, "/open")).status == Outcome.SUCCESS
        for _ in range(2):
            denied = await get(web, "/private/x")
            assert (denied.status, denied.issue_code) == (
                Outcome.ACCESS_RESTRICTION,
                "robots_denied",
            )

    with robots_cache():
        run_async(run())
    assert transport.robots_requests == 1
    assert not any("/private" in url for url in transport.urls)


def test_a_refused_robots_answer_is_cached_with_its_outcome():
    transport = Transport(HttpResponse(200, {"content-type": "text/html"}, b"<html>", 6))
    web = acquirer(transport)

    async def run():
        for path in ("/a", "/b"):
            result = await get(web, path)
            assert (result.status, result.issue_code) == (
                Outcome.ACCESS_RESTRICTION,
                "invalid_robots",
            )

    with robots_cache():
        run_async(run())
    assert transport.robots_requests == 1
    assert not any(url.endswith(("/a", "/b")) for url in transport.urls)


def test_the_operator_grant_is_checked_before_the_cache():
    body = b"User-agent: *\nDisallow: /\n"
    transport = Transport(HttpResponse(200, {"content-type": "text/plain"}, body, len(body)))
    web = acquirer(transport)

    async def run():
        denied = await get(web, "/a")
        assert denied.issue_code == "robots_denied"
        with operator_trust(ignore_robots=[HOST]):
            granted = await get(web, "/a")
        assert granted.status == Outcome.SUCCESS and granted.robots_overridden

    with robots_cache():
        run_async(run())
    assert transport.robots_requests == 1


@pytest.mark.parametrize("sources", [2, 5])
def test_concurrent_sources_share_one_fetch(sources):
    transport = Transport(not_found())
    web = acquirer(transport)

    async def run():
        results = await asyncio.gather(*(get(web, f"/p{n}") for n in range(sources)))
        assert all(r.status == Outcome.SUCCESS for r in results)

    with robots_cache():
        run_async(run())
    assert transport.robots_requests == 1


def test_a_nested_scope_never_reuses_the_outer_cache():
    with robots_cache() as outer, robots_cache() as inner:
        assert inner is not outer
