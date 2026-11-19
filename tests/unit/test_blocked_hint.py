"""A refused source carries a hint naming the kind of block and the guide section for it."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro import AcquisitionPolicy, Outcome, Source
from rastro.application.acquisition import WebAcquirer
from rastro.application.blocked_hint import blocked_hint, response_marker
from rastro.domain.acquisition import HttpResponse

ROOT = Path(__file__).resolve().parents[2]


def response(status, body=b"", **headers):
    return HttpResponse(status, {"content-type": "text/html", **headers}, body, len(body))


class Resolver:
    async def resolve(self, host, port):
        return ("8.8.8.8",)


class Transport:
    def __init__(self, page):
        self.page = page

    async def get(self, target, **kwargs):
        if urlsplit(target.url).path == "/robots.txt":
            return response(404)
        return self.page


async def fetch(page):
    client = WebAcquirer(
        AcquisitionPolicy(allowed_hosts=("example.com",), min_interval_seconds=0, max_retries=0),
        transport=Transport(page),
        resolver=Resolver(),
    )
    return await client.acquire(Source(url="https://example.com/p"), max_bytes=5000)


@pytest.mark.asyncio
async def test_cloud_ip_403_hint():
    body = b"<h1>Access denied</h1><p>Error code: 1005. The site owner banned your network.</p>"
    result = await fetch(response(403, body))
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.blocked is not None
    assert result.blocked.kind == "cloud_ip_refused"
    assert result.blocked.http_status == 403
    assert result.blocked.docs == "docs/egress.md#cloud-ip-refused"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "kind", "anchor"),
    [
        (response(403, b"<p>no</p>"), "unknown", "blocked"),
        (response(401, b"", **{"www-authenticate": "Basic"}), "login_wall", "login-wall"),
        (response(403, b"", **{"cf-mitigated": "challenge"}), "challenge_unsolved", "challenge"),
        (response(429, b"", **{"retry-after": "5"}), "rate_limited", "rate-limited"),
        (response(451, b""), "geo_blocked", "geo-blocked"),
    ],
)
async def test_refusals_map_to_kinds(page, kind, anchor):
    result = await fetch(page)
    assert result.blocked is not None
    assert (result.blocked.kind, result.blocked.docs) == (kind, f"docs/egress.md#{anchor}")


@pytest.mark.asyncio
async def test_success_and_ordinary_failures_carry_no_hint():
    assert (await fetch(response(200, b"<p>ok</p>"))).blocked is None
    assert (await fetch(response(404, b""))).blocked is None


def test_issue_codes_alone_give_hints():
    robots = blocked_hint("robots_denied", None, None)
    assert robots is not None and (robots.kind, robots.docs) == ("robots", "docs/egress.md#robots")
    challenge = blocked_hint("challenge_unsolved", 200, None)
    assert challenge is not None and challenge.kind == "challenge_unsolved"
    assert blocked_hint("network_failure", None, None) is None


def test_markers_only_read_refusals():
    assert response_marker(response(200, b"Error code: 1005")) is None
    assert response_marker(response(403, b"Error code: 1005")) == "cloud_ip_refused"


def test_every_hint_anchor_exists_in_the_guide():
    from rastro.application.blocked_hint import ANCHORS

    guide = (ROOT / "docs/egress.md").read_text(encoding="utf-8").lower()
    headings = {
        "-".join(line.lstrip("#").strip().replace("(", "").replace(")", "").split())
        for line in guide.splitlines()
        if line.startswith("#")
    }
    for anchor in ANCHORS.values():
        assert anchor in headings, anchor
