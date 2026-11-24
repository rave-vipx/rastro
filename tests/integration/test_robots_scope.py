"""One user action fetches robots.txt at most once per origin.

Every public application entry that acquires pages enters ``robots_cache()``; two separate
actions never share an answer, and an ``ignore_robots`` host is never asked. Offline: a fake
transport counts the requests.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.job_from_url import html_from_url
from rastro.application.job_new_sites import new_job_from_sites
from rastro.application.job_repair import run_sync
from rastro.application.operator_trust import operator_trust
from rastro.application.source_proposals import acquire_approved_pages
from rastro.domain.acquisition import (
    AcquisitionPolicy,
    HttpResponse,
    PinnedTarget,
    ProxyRoute,
)

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

PAGE = """<html><body>
<article class="event"><h2 class="title">Concierto</h2><span class="venue">Solís</span></article>
<article class="event"><h2 class="title">Ballet</h2><span class="venue">Adela Reta</span></article>
<article class="event"><h2 class="title">Milonga</h2><span class="venue">Plaza</span></article>
</body></html>"""


class Transport:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.robots_body = b"User-agent: *\nAllow: /\n"

    @property
    def robots(self) -> list[str]:
        return [u for u in self.urls if urlsplit(u).path == "/robots.txt"]

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
            body = self.robots_body
            return HttpResponse(200, {"content-type": "text/plain"}, body, len(body))
        body = PAGE.encode()
        return HttpResponse(200, {"content-type": "text/html"}, body, len(body))


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


def acquirer(transport: Transport, *hosts: str) -> WebAcquirer:
    policy = AcquisitionPolicy(allowed_hosts=hosts, min_interval_seconds=0.0)
    return WebAcquirer(policy, transport=transport, resolver=Resolver())


def test_new_job_from_sites_over_three_pages_asks_robots_once(tmp_path: Path) -> None:
    transport = Transport()
    new_job_from_sites(
        tmp_path / "job.toml",
        sites=[f"https://events.example/p{i}" for i in range(1, 4)],
        acquisition=acquirer(transport, "events.example"),
        fields=("title", "venue"),
    )
    assert len(transport.urls) == 4
    assert len(transport.robots) == 1


def test_approval_fetching_three_pages_per_host_asks_robots_once_per_host() -> None:
    transport = Transport()
    urls = [f"https://{host}/p{i}" for host in ("a.example", "b.example") for i in range(1, 5)]
    _, _, results = acquire_approved_pages(
        urls, ["a.example", "b.example"], acquisition=acquirer(transport, "a.example", "b.example")
    )
    assert len(results) == 6  # at most three pages per host
    assert sorted(transport.robots) == [
        "https://a.example/robots.txt",
        "https://b.example/robots.txt",
    ]


def test_url_start_asks_robots_once() -> None:
    transport = Transport()
    html_from_url("https://events.example/list", acquisition=acquirer(transport, "events.example"))
    assert len(transport.robots) == 1


def test_a_repair_fetching_the_source_twice_asks_robots_once() -> None:
    transport = Transport()
    client = acquirer(transport, "events.example")

    async def fetch_twice() -> None:
        from rastro.domain.models import Source

        for _ in range(2):
            await client.acquire(Source(url="https://events.example/list"), max_bytes=100_000)

    run_sync(fetch_twice())
    assert len(transport.robots) == 1


def test_separate_operations_do_not_share_robots_answers() -> None:
    transport = Transport()
    client = acquirer(transport, "events.example")
    html_from_url("https://events.example/a", acquisition=client)
    html_from_url("https://events.example/b", acquisition=client)
    assert len(transport.robots) == 2


def test_a_url_start_inside_an_outer_cache_asks_again_and_honours_a_new_denial() -> None:
    """An inherited, already-populated cache must not carry an old allow into a new operation."""
    from rastro.application.robots_cache import robots_cache
    from rastro.domain.models import ConfigurationError

    transport = Transport()
    client = acquirer(transport, "events.example")
    with robots_cache():
        html_from_url("https://events.example/a", acquisition=client)  # outer allow is cached
        assert len(transport.robots) == 1
        transport.robots_body = b"User-agent: *\nDisallow: /\n"
        with pytest.raises(ConfigurationError, match="Could not fetch"):
            html_from_url("https://events.example/b", acquisition=client)
    assert len(transport.robots) == 2


def test_ignored_robots_host_is_never_asked() -> None:
    transport = Transport()
    with operator_trust(ignore_robots=["events.example"]):
        acquire_approved_pages(
            [f"https://events.example/p{i}" for i in range(1, 4)],
            ["events.example"],
            acquisition=acquirer(transport, "events.example"),
        )
    assert transport.robots == []
