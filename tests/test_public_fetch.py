"""rastro.fetch: one URL under the job acquirer's policy, explicit outcomes.

Offline: the real ``WebAcquirer`` runs against a fake transport and resolver, so robots.txt,
the host allowlist and the byte bound are exercised exactly as for ``rastro.scrape`` URLs.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest

import rastro
from rastro import AcquisitionPolicy, ConfigurationError, Outcome
from rastro.application.acquisition import WebAcquirer
from rastro.domain.acquisition import AcquisitionError, HttpResponse, PinnedTarget, ProxyRoute

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

PAGE = "<html><body><h1>Fiesta</h1></body></html>"


class Transport:
    def __init__(self, robots: bytes = b"User-agent: *\nAllow: /\n", body: str = PAGE) -> None:
        self.robots = robots
        self.body = body.encode()
        self.urls: list[str] = []

    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None,
    ) -> HttpResponse:
        self.urls.append(target.url)
        if urlsplit(target.url).path == "/robots.txt":
            return HttpResponse(200, {"content-type": "text/plain"}, self.robots, len(self.robots))
        if len(self.body) > max_bytes:  # the real transport stops reading at the bound
            raise AcquisitionError("body_too_large", Outcome.PERMANENT_FAILURE)
        kind = "application/json" if self.body.startswith(b"{") else "text/html"
        return HttpResponse(200, {"content-type": kind}, self.body, len(self.body))


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Transport:
    fake = Transport()
    seen: list[AcquisitionPolicy] = []

    def build(policy: AcquisitionPolicy) -> WebAcquirer:
        seen.append(policy)
        fast = policy.model_copy(update={"min_interval_seconds": 0.0})
        return WebAcquirer(fast, transport=fake, resolver=Resolver())

    monkeypatch.setattr("rastro.fetch_api.web_acquirer", build)
    fake.policies = seen  # type: ignore[attr-defined]
    return fake


def test_fetch_returns_the_document_under_a_single_host_default_policy(transport):
    fetched = rastro.fetch("https://events.example/fiesta?id=7")
    assert fetched.status == Outcome.SUCCESS
    assert fetched.body == PAGE
    assert fetched.media_type == "text/html"
    assert fetched.url == "https://events.example/fiesta?id=7"
    assert fetched.issues == ()
    assert transport.policies[0].allowed_hosts == ("events.example",)
    assert transport.urls == ["https://events.example/robots.txt", fetched.url]


def test_fetch_json_representation(transport):
    transport.body = b'{"lots": []}'
    fetched = rastro.fetch(
        "https://events.example/api/tickets",
        policy=AcquisitionPolicy(allowed_hosts=("events.example",)),
        representation="json",
    )
    assert (fetched.status, fetched.body, fetched.media_type) == (
        Outcome.SUCCESS,
        '{"lots": []}',
        "application/json",
    )


def test_robots_denied_is_an_explicit_failure_not_an_exception(transport):
    transport.robots = b"User-agent: *\nDisallow: /\n"
    fetched = rastro.fetch("https://events.example/fiesta")
    assert fetched.status == Outcome.ACCESS_RESTRICTION
    assert fetched.body is None and fetched.media_type is None
    assert [issue.code for issue in fetched.issues] == ["robots_denied"]
    assert transport.urls == ["https://events.example/robots.txt"]


def test_invalid_robots_is_an_explicit_failure(transport):
    transport.robots = b"\xff\xfe not utf-8 \x00"
    fetched = rastro.fetch("https://events.example/fiesta")
    assert fetched.status == Outcome.ACCESS_RESTRICTION
    assert [issue.code for issue in fetched.issues] == ["invalid_robots"]


def test_host_outside_the_policy_fails_without_any_request(transport):
    policy = AcquisitionPolicy(allowed_hosts=("events.example",))
    fetched = rastro.fetch("https://other.example/page", policy=policy)
    assert fetched.status != Outcome.SUCCESS
    assert fetched.body is None
    assert fetched.issues and fetched.issues[0].code
    assert transport.urls == []


def test_oversized_body_fails_at_max_bytes(transport):
    fetched = rastro.fetch("https://events.example/fiesta", max_bytes=len(PAGE) - 1)
    assert fetched.status == Outcome.PERMANENT_FAILURE
    assert fetched.body is None
    assert [issue.code for issue in fetched.issues] == ["body_too_large"]
    assert rastro.fetch("https://events.example/fiesta", max_bytes=len(PAGE)).body == PAGE


def test_blank_document_is_never_a_successful_empty_body(transport):
    transport.body = b"   "
    fetched = rastro.fetch("https://events.example/fiesta")
    assert fetched.status == Outcome.INVALID_DATA
    assert fetched.body is None
    assert [issue.code for issue in fetched.issues] == ["empty_document"]


@pytest.mark.parametrize(
    ("url", "options"),
    [
        ("not a url", {}),
        ("https://user:pw@events.example/", {}),
        ("https://events.example/", {"max_bytes": 0}),
        ("https://events.example/", {"max_bytes": True}),
        ("https://events.example/", {"representation": "xml"}),
        (
            "https://events.example/",
            {"policy": AcquisitionPolicy(allowed_hosts=("events.example",), render="browser")},
        ),
    ],
)
def test_caller_mistakes_raise_configuration_error(transport, url, options):
    with pytest.raises(ConfigurationError):
        rastro.fetch(url, **options)
    assert transport.urls == []


def test_fetch_refuses_a_running_loop_and_fetch_async_works_there(transport):
    async def inside() -> rastro.Fetched:
        with pytest.raises(ConfigurationError, match="fetch_async"):
            rastro.fetch("https://events.example/fiesta")
        return await rastro.fetch_async("https://events.example/fiesta")

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        assert runner.run(inside()).body == PAGE


def test_fetched_text_decodes(transport):
    html = "<html><body><h1>Fiesta — música</h1></body></html>"
    transport.body = html.encode("utf-8")
    fetched = rastro.fetch("https://events.example/fiesta")
    assert fetched.status == Outcome.SUCCESS
    assert fetched.text == html
    assert fetched.text == fetched.body
    transport.robots = b"User-agent: *\nDisallow: /\n"
    failed = rastro.fetch("https://events.example/fiesta")
    assert failed.status == Outcome.ACCESS_RESTRICTION
    assert failed.text is None
