"""Synthetic multi-address acquisition: SEC-001/002/003."""

import asyncio

import pytest
from test_acquisition import Resolver, Transport, client, response

from rastro import Outcome, Source
from rastro.domain.acquisition import AcquisitionError

pytestmark = pytest.mark.asyncio
ADDRESSES = ("2606:4700:4700::1111", "8.8.8.8", "1.1.1.1", "8.8.4.4")
SOURCE = Source(url="https://example.com/")


def connect_failure(code="request_timeout"):
    return AcquisitionError(code, Outcome.TRANSIENT_FAILURE, connect_failure=True)


async def test_ipv6_connect_timeout_falls_back_to_ipv4_and_succeeds():
    transport = Transport([connect_failure(), response()])
    resolver = Resolver(ADDRESSES[:2])
    result = await client(transport, resolver, max_retries=0).acquire(SOURCE, max_bytes=100)
    assert result.status == Outcome.SUCCESS
    assert result.body == "<p>ok</p>"
    assert [target.address for target, _ in transport.calls] == [*ADDRESSES[:1], *ADDRESSES[:2]]
    assert transport.calls[-1][0].url == SOURCE.url
    assert result.requests == 3 and result.retries == 0 and resolver.calls == 2


@pytest.mark.parametrize("position", range(5))
async def test_private_address_anywhere_in_answer_still_rejects_whole_answer(position):
    addresses = (*ADDRESSES[:position], "127.0.0.1", *ADDRESSES[position:])
    transport = Transport()
    result = await client(transport, Resolver(addresses)).acquire(SOURCE, max_bytes=100)
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.issue_code == "destination_denied"
    assert not transport.calls


async def test_no_fallback_after_http_response():
    transport = Transport([response(503), response()])
    result = await client(transport, Resolver(ADDRESSES), max_retries=0).acquire(
        SOURCE, max_bytes=100
    )
    assert result.http_status == 503 and result.issue_code == "http_transient_failure"
    assert result.requests == 2
    assert [target.address for target, _ in transport.calls] == [ADDRESSES[0]] * 2


async def test_fallback_attempts_count_as_requests_and_respect_operation_timeout():
    class SlowFallback(Transport):
        async def get(self, target, **kwargs):
            if target.address == ADDRESSES[1]:
                self.calls.append((target, kwargs))
                await asyncio.Event().wait()
            return await super().get(target, **kwargs)

    transport = SlowFallback([connect_failure()])
    result = await client(
        transport,
        Resolver(ADDRESSES),
        max_retries=0,
        request_timeout_seconds=0.1,
        operation_timeout_seconds=0.1,
    ).acquire(SOURCE, max_bytes=100)
    assert result.issue_code == "operation_timeout"
    assert result.requests == len(transport.calls) == 3
    assert result.network_seconds > 0


@pytest.mark.parametrize("limit", [2, 3])
async def test_fallback_cannot_exceed_request_budget(limit):
    transport = Transport([connect_failure()] * 3)
    result = await client(
        transport, Resolver(ADDRESSES), max_requests=limit, max_retries=0
    ).acquire(SOURCE, max_bytes=100)
    assert result.issue_code == "request_limit"
    assert result.requests == len(transport.calls) == limit


async def test_at_most_three_addresses_are_tried_in_dns_order():
    transport = Transport([connect_failure("network_failure")] * 3 + [response()])
    result = await client(transport, Resolver(ADDRESSES), max_retries=0).acquire(
        SOURCE, max_bytes=100
    )
    assert result.issue_code == "network_failure"
    assert [target.address for target, _ in transport.calls[1:]] == list(ADDRESSES[:3])
    assert result.requests == 4


@pytest.mark.parametrize("failure", [False, True])
async def test_single_address_behavior_unchanged(failure):
    transport = Transport([connect_failure()] if failure else [response()])
    result = await client(transport, Resolver(ADDRESSES[:1]), max_retries=0).acquire(
        SOURCE, max_bytes=100
    )
    assert result.issue_code == ("request_timeout" if failure else None)
    assert result.requests == 2 and result.retries == 0
    assert transport.calls[-1][0].alternates == ()


async def test_generic_transfer_failure_does_not_fall_back():
    transport = Transport([AcquisitionError("request_timeout", Outcome.TRANSIENT_FAILURE)])
    result = await client(transport, Resolver(ADDRESSES), max_retries=0).acquire(
        SOURCE, max_bytes=100
    )
    assert result.issue_code == "request_timeout" and result.requests == 2


async def test_proxy_does_not_fall_back_between_locally_resolved_addresses():
    transport = Transport([connect_failure()])
    result = await client(
        transport,
        Resolver(ADDRESSES),
        max_retries=0,
        egress_profiles={"office": "http://proxy.example"},
    ).acquire(SOURCE.model_copy(update={"egress": "office"}), max_bytes=100)
    assert result.issue_code == "request_timeout" and result.requests == 2
    assert all(options["proxy"].name == "office" for _, options in transport.calls)


async def test_fallback_on_robots_lookup_obeys_rules():
    class RobotsFallback(Transport):
        async def get(self, target, **kwargs):
            if target.address == ADDRESSES[0]:
                self.calls.append((target, kwargs))
                raise connect_failure()
            return await super().get(target, **kwargs)

    transport = RobotsFallback(
        robots=response(body=b"User-agent: *\nDisallow: /", **{"content-type": "text/plain"})
    )
    result = await client(transport, Resolver(ADDRESSES), max_retries=0).acquire(
        SOURCE, max_bytes=100
    )
    assert result.issue_code == "robots_denied"
    assert result.requests == 2
    assert all(target.url.endswith("/robots.txt") for target, _ in transport.calls)
