"""SCHED-005, SEC-005: cookies are bounded, host-only and run-local."""

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.cookie_jar import CookieJar
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse
from rastro.domain.models import Source


def test_host_secure_path_expiry_and_redaction():
    jar = CookieJar()
    jar.receive(
        "https://a.example/list",
        "sid=synthetic-secret; Domain=example; Secure\nother=two; Path=/list",
    )
    assert jar.header("https://a.example/list") == "sid=synthetic-secret; other=two"
    assert jar.header("https://b.example/list") == ""
    assert jar.header("http://a.example/list") == "other=two"
    assert jar.header("https://a.example/else") == "sid=synthetic-secret"
    assert "synthetic-secret" not in repr(jar)
    jar.receive(
        "https://a.example/list",
        "sid=gone; Max-Age=0\nother=gone; Path=/list; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    )
    assert jar.header("https://a.example/list") == ""


def test_limits_count_drops_without_echoing_values():
    jar = CookieJar()
    jar.receive("https://a.example/", "large=" + "x" * 4096)
    for index in range(51):
        jar.receive("https://a.example/", f"c{index}=v")
    assert len(jar.header("https://a.example/").split("; ")) == 50
    assert jar.dropped == 2
    jar.receive("https://a.example/", "c0=replaced")
    assert "c0=replaced" in jar.header("https://a.example/")
    assert "large" not in repr(jar)


class Resolver:
    async def resolve(self, host, port):
        return ("8.8.8.8",)


class Transport:
    def __init__(self):
        self.calls = []

    async def get(self, target, *, headers, **kwargs):
        self.calls.append((target.url, dict(headers)))
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        if target.url.endswith("/redirect"):
            return HttpResponse(
                302, {"location": "https://b.example/next", "set-cookie": "sid=secret"}, b"", 0
            )
        return HttpResponse(
            200,
            {"content-type": "text/html", "set-cookie": "sid=secret\nsecond=yes"},
            b"<p>ok</p>",
            9,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_acquirer_sessions_host_boundaries_and_default(enabled, caplog):
    transport = Transport()
    client = WebAcquirer(
        AcquisitionPolicy(
            allowed_hosts=("a.example", "b.example"), session=enabled, min_interval_seconds=0
        ),
        transport=transport,
        resolver=Resolver(),
    )
    for path in ("/first", "/second", "/redirect"):
        result = await client.acquire(Source(url="https://a.example" + path), max_bytes=100)
        assert result.status == "success"
    documents = [
        (url, headers) for url, headers in transport.calls if not url.endswith("robots.txt")
    ]
    assert (documents[1][1].get("Cookie") == "sid=secret; second=yes") is enabled
    assert "Cookie" not in documents[-1][1]
    assert all("Cookie" not in h for u, h in transport.calls if "b.example" in u)
    assert "secret" not in repr(client) + repr(result) + caplog.text
    assert "secret" not in repr(HttpResponse(200, {"set-cookie": "secret"}, b"", 0))
    fresh = WebAcquirer(
        AcquisitionPolicy(allowed_hosts=("a.example",), min_interval_seconds=0),
        transport=Transport(),
        resolver=Resolver(),
    )
    assert not fresh.policy.session


@pytest.mark.asyncio
async def test_cross_host_redirect_withholds_even_preexisting_target_cookies():
    transport = Transport()
    policy = AcquisitionPolicy(
        allowed_hosts=("a.example", "b.example"), session=True, min_interval_seconds=0
    )
    client = WebAcquirer(policy, transport=transport, resolver=Resolver())
    await client.acquire(Source(url="https://b.example/first"), max_bytes=100)
    transport.calls.clear()
    await client.acquire(Source(url="https://a.example/redirect"), max_bytes=100)
    assert all("Cookie" not in headers for url, headers in transport.calls if "b.example" in url)
    independent = WebAcquirer(policy, transport=Transport(), resolver=Resolver())
    assert independent._cookies.header("https://b.example/") == ""


@pytest.mark.parametrize(
    "value",
    [
        "not a cookie",
        "one=x; Max-Age=invalid",
        "x=one\rbad=two",
        "a=1; Expires=invalid",
        "a=1; b=2",
    ],
)
def test_malformed_cookies_are_dropped_without_diagnostics(value):
    jar = CookieJar()
    jar.receive("https://a.example/", value)
    assert jar.header("https://a.example/") == ""
    assert jar.dropped == 1


def test_expiry_prunes_and_path_does_not_match_prefix(monkeypatch):
    import rastro.application.cookie_jar as module

    clock = 1000
    monkeypatch.setattr(module, "time", lambda: clock)
    jar = CookieJar()
    jar.receive("https://a.example/a/list", "one=yes; Max-Age=5; Path=/a\ntwo=yes; Path=relative")
    assert jar.header("https://a.example/ab") == "two=yes"
    clock = 1006
    assert jar.header("https://a.example/a/list") == "two=yes"
    jar.receive("https://a.example/a", "")
    assert jar.dropped == 0
