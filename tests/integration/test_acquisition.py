import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import urlsplit

import pytest

from rastro import AcquisitionPolicy, Outcome, Source
from rastro.application.acquisition import WebAcquirer, retry_after
from rastro.domain.acquisition import AcquisitionError, HttpResponse


class Resolver:
    def __init__(self, addresses=("8.8.8.8",)):
        self.addresses = addresses
        self.calls = 0

    async def resolve(self, host, port):
        self.calls += 1
        return self.addresses


def response(status=200, body=b"<p>ok</p>", **headers):
    return HttpResponse(status, {"content-type": "text/html", **headers}, body, len(body))


class Transport:
    def __init__(self, pages=None, robots=None):
        self.pages = list(pages or [response()])
        self.robots = robots or response(404)
        self.calls = []

    async def get(self, target, **kwargs):
        self.calls.append((target, kwargs))
        if urlsplit(target.url).path == "/robots.txt":
            return self.robots
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def client(transport=None, resolver=None, **overrides):
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com", "other.test"),
        min_interval_seconds=0,
        backoff_seconds=0.001,
        **overrides,
    )
    return WebAcquirer(policy, transport=transport or Transport(), resolver=resolver or Resolver())


pytestmark = pytest.mark.asyncio


async def test_success_preserves_regional_settings_and_counts():
    transport = Transport()
    result = await client(transport).acquire(
        Source(url="https://example.com/x", locale="es-UY"), max_bytes=100
    )
    assert result.status == Outcome.SUCCESS
    assert result.html == "<p>ok</p>"
    assert result.requests == 2
    assert result.bytes_received == 18
    assert result.acquisition_seconds >= result.network_seconds >= 0
    target, options = transport.calls[-1]
    assert target.address == "8.8.8.8"
    assert options["headers"]["Accept-Language"] == "es-UY"
    assert options["headers"]["User-Agent"].startswith("Rastro/")


async def test_ordered_language_preferences_override_locale_header():
    transport = Transport()
    result = await client(transport).acquire(
        Source(
            url="https://example.com/x",
            locale="es-UY",
            languages=tuple(f"a{letter}" for letter in "abcdefghijklmnopqrst"),
        ),
        max_bytes=100,
    )
    assert result.status == Outcome.SUCCESS
    assert transport.calls[-1][1]["headers"]["Accept-Language"] == (
        "aa, ab;q=0.95, ac;q=0.9, ad;q=0.85, ae;q=0.8, af;q=0.75, ag;q=0.7, "
        "ah;q=0.65, ai;q=0.6, aj;q=0.55, ak;q=0.5, al;q=0.45, am;q=0.4, "
        "an;q=0.35, ao;q=0.3, ap;q=0.25, aq;q=0.2, ar;q=0.15, as;q=0.1, at;q=0.05"
    )


@pytest.mark.parametrize(
    ("page", "code", "outcome"),
    [
        (response(403), "access_restricted", Outcome.ACCESS_RESTRICTION),
        (response(429, **{"retry-after": "3600"}), "rate_limited", Outcome.ACCESS_RESTRICTION),
        (response(503), "http_transient_failure", Outcome.TRANSIENT_FAILURE),
        (response(404), "http_permanent_failure", Outcome.PERMANENT_FAILURE),
        (response(206), "http_permanent_failure", Outcome.PERMANENT_FAILURE),
        (
            response(**{"content-type": "application/pdf"}),
            "unsupported_content_type",
            Outcome.PERMANENT_FAILURE,
        ),
        (response(body=b"\xff"), "invalid_encoding", Outcome.INVALID_DATA),
        (
            response(**{"content-type": "text/html; charset=bad"}),
            "invalid_encoding",
            Outcome.INVALID_DATA,
        ),
        (response(302), "invalid_redirect", Outcome.PERMANENT_FAILURE),
        (
            response(302, location="http://example.com/"),
            "https_downgrade",
            Outcome.ACCESS_RESTRICTION,
        ),
        (
            response(302, location="https://private.test/"),
            "host_not_allowed",
            Outcome.ACCESS_RESTRICTION,
        ),
    ],
)
async def test_http_failures(page, code, outcome):
    result = await client(Transport([page]), max_retries=0, allow_http=True).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == code
    assert result.status == outcome
    assert result.html is None


@pytest.mark.parametrize(
    "robots",
    [
        response(body=b"User-agent: *\nDisallow: /private", **{"content-type": "text/plain"}),
        response(body=b"\xff", **{"content-type": "text/plain"}),
        response(),
        response(403),
        response(503),
        response(429),
    ],
)
async def test_robots_fail_closed(robots):
    transport = Transport(robots=robots)
    result = await client(transport, max_retries=0).acquire(
        Source(url="https://example.com/private"), max_bytes=100
    )
    assert result.status != Outcome.SUCCESS
    assert len(transport.calls) == 1


async def test_robots_rules_redirects_and_crawl_delay():
    transport = Transport(
        robots=response(
            body=b"User-agent: *\nDisallow: /no\nCrawl-delay: 0.001\nRequest-rate: 100/1\n",
            **{"content-type": "text/plain"},
        )
    )
    acquirer = client(transport)
    result = await acquirer.acquire(Source(url="https://example.com/ok"), max_bytes=100)
    assert result.status == Outcome.SUCCESS
    assert result.acquisition_seconds >= 1
    redirect = Transport(robots=response(302, location="https://private.test/robots.txt"))
    result = await client(redirect).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "host_not_allowed"
    loop = Transport(robots=response(302, location="/robots.txt"))
    result = await client(loop, max_redirects=1).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == "robots_redirect_limit"
    assert result.requests == 2


async def test_redirects_revalidate_dns_and_check_new_robots():
    resolver = Resolver()
    transport = Transport([response(302, location="https://other.test/final"), response()])
    result = await client(transport, resolver).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.url == "https://other.test/final"
    assert result.requests == resolver.calls == 4
    assert result.redirects == 1
    loop = Transport([response(302, location="/")])
    result = await client(loop, max_redirects=0).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == "redirect_limit"


async def test_dns_rebinding_between_robots_and_document_is_blocked():
    resolver = Resolver()

    class Rebinding(Transport):
        async def get(self, target, **kwargs):
            resolver.addresses = ("127.0.0.1",)
            return await super().get(target, **kwargs)

    transport = Rebinding()
    result = await client(transport, resolver).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == "destination_denied"
    assert len(transport.calls) == 1


async def test_retries_are_bounded_and_resolve_again():
    transport = Transport(
        [response(503), AcquisitionError("network_failure", Outcome.TRANSIENT_FAILURE), response()]
    )
    resolver = Resolver()
    result = await client(transport, resolver).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.status == Outcome.SUCCESS
    assert result.retries == 2
    assert result.requests == resolver.calls == 4
    transport = Transport([AcquisitionError("request_timeout", Outcome.TRANSIENT_FAILURE)])
    result = await client(transport, max_retries=0).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == "request_timeout"


@pytest.mark.parametrize("status", [403, 429, 503])
async def test_restrictions_and_retry_after_are_shared_without_retries(status):
    transport = Transport([response(status, **{"retry-after": "3600"})])
    acquirer = client(transport)
    first = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    second = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert first.status != Outcome.SUCCESS
    assert second.status == Outcome.ACCESS_RESTRICTION
    assert len(transport.calls) == 2
    assert first.retries == second.requests == 0
    if status != 403:
        assert first.retry_after_seconds == 3600
        assert second.retry_after_seconds > 3500


async def test_operation_request_and_count_limits():
    result = await client(max_requests=1).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "request_limit"

    class SlowResolver:
        async def resolve(self, host, port):
            await asyncio.sleep(1)
            return ("8.8.8.8",)

    result = await client(
        resolver=SlowResolver(),
        request_timeout_seconds=0.05,
        operation_timeout_seconds=1,
        max_retries=0,
    ).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "request_timeout"
    result = await client(
        resolver=SlowResolver(), request_timeout_seconds=0.1, operation_timeout_seconds=0.25
    ).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "operation_timeout"


async def test_cancellation_releases_host_and_global_slots():
    entered = asyncio.Event()

    class Paused(Transport):
        async def get(self, target, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    acquirer = client(Paused())
    task = asyncio.create_task(acquirer.acquire(Source(url="https://example.com/"), max_bytes=100))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    acquirer._transport = Transport()
    result = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.status == Outcome.SUCCESS


@pytest.mark.parametrize(
    "source",
    [
        Source(url="https://example.com/", locale="en\r\nX: 1"),
        Source(url="https://example.com/").model_copy(update={"languages": ("en\r\nX: 1",)}),
        Source(url="https://example.com/").model_copy(update={"languages": ("en",) * 21}),
    ],
)
async def test_invalid_language_header_is_rejected_before_dns(source):
    resolver = Resolver()
    result = await client(resolver=resolver).acquire(source, max_bytes=100)
    assert result.issue_code == "invalid_locale"
    assert resolver.calls == 0


async def test_unsupported_egress_is_rejected_before_dns():
    resolver = Resolver()
    source = Source(url="https://example.com/").model_copy(update={"egress": "proxy"})
    result = await client(resolver=resolver).acquire(source, max_bytes=100)
    assert result.issue_code == "unsupported_egress"
    assert result.status == Outcome.PERMANENT_FAILURE
    assert resolver.calls == 0


async def test_retry_after_formats():
    assert retry_after("12") == 12
    assert 58 < retry_after(format_datetime(datetime.now(UTC) + timedelta(seconds=60))) <= 60
    assert retry_after("Thu, 01 Jan 1970 00:00:00 GMT") == 0
    assert retry_after("9" * 100) > 1e90
    for invalid in (None, "no", "-1", "Thu, 01 Jan 1970 00:00:00"):
        assert retry_after(invalid) is None


async def test_retry_after_is_honored_even_without_retry_budget():
    transport = Transport([response(503, **{"retry-after": "3600"})])
    acquirer = client(transport, max_retries=0)
    first = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    second = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert first.retry_after_seconds == 3600
    assert second.requests == 0


async def test_malformed_redirect_is_an_explicit_failure():
    result = await client(Transport([response(302, location="https://[invalid")])).acquire(
        Source(url="https://example.com/"), max_bytes=100
    )
    assert result.issue_code == "invalid_redirect"


async def test_global_and_per_host_concurrency_are_enforced():
    class Measured(Transport):
        active = 0
        peak = 0
        host_peak = 0

        def __init__(self):
            super().__init__([response()] * 6)
            self.by_host = {}

        async def get(self, target, **kwargs):
            self.active += 1
            self.by_host[target.host] = self.by_host.get(target.host, 0) + 1
            self.peak = max(self.peak, self.active)
            self.host_peak = max(self.host_peak, self.by_host[target.host])
            try:
                await asyncio.sleep(0.01)
                return await super().get(target, **kwargs)
            finally:
                self.active -= 1
                self.by_host[target.host] -= 1

    transport = Measured()
    acquirer = client(transport, max_concurrency=2)
    results = await asyncio.gather(
        *[
            acquirer.acquire(Source(url=f"https://{host}/"), max_bytes=100)
            for host in ("example.com", "example.com", "other.test")
        ]
    )
    assert all(result.status == Outcome.SUCCESS for result in results)
    assert transport.peak == 2 and transport.host_peak == 1
    transport.peak = 0
    acquirer = client(transport, max_concurrency=1)
    await asyncio.gather(
        *[
            acquirer.acquire(Source(url=f"https://{host}/"), max_bytes=100)
            for host in ("example.com", "other.test")
        ]
    )
    assert transport.peak == 1


async def test_same_origin_redirect_reuses_robots_within_one_operation():
    transport = Transport([response(302, location="/next"), response()])
    result = await client(transport).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.status == Outcome.SUCCESS
    assert result.requests == 3


async def test_invalid_retry_after_defers_conservatively():
    transport = Transport([response(503, **{"retry-after": "invalid"})])
    result = await client(transport).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "retry_after_pending"
    assert result.retry_after_seconds == 30
    assert result.retries == 0


async def test_robots_expansion_is_rejected_before_the_parser(monkeypatch):
    from protego import Protego

    body = (
        "\n".join(f"User-agent: bot{i}" for i in range(600))
        + "\n"
        + "\n".join(f"Disallow: /p{i}" for i in range(600))
    ).encode()

    def forbidden_parse(*args):
        pytest.fail("Expansive input reached the parser")

    monkeypatch.setattr(Protego, "parse", forbidden_parse)
    transport = Transport(robots=response(body=body, **{"content-type": "text/plain"}))
    result = await client(transport).acquire(Source(url="https://example.com/"), max_bytes=100)
    assert result.issue_code == "robots_limit"


@pytest.mark.parametrize(
    "directive",
    [
        "Request-rate: 1/" + "9" * 400,
        "Crawl-delay: NaN",
        "Crawl-delay: inf",
        "Crawl-delay: -1",
        "Request-rate: -1/10",
        "Request-rate: 0/10",
    ],
    ids=["huge-rate", "nan-delay", "inf-delay", "negative-delay", "negative-rate", "zero-rate"],
)
async def test_invalid_robots_intervals_cannot_poison_shared_host(directive):
    transport = Transport(
        robots=response(
            body=("User-agent: *\n" + directive).encode(), **{"content-type": "text/plain"}
        )
    )
    acquirer = client(transport, request_timeout_seconds=0.02, operation_timeout_seconds=0.05)
    denied = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert denied.issue_code == "invalid_robots"
    transport.robots = response(404)
    good = await acquirer.acquire(Source(url="https://example.com/"), max_bytes=100)
    assert good.status == Outcome.SUCCESS
