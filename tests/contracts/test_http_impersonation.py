"""Browser impersonation and HTTP/3 keep every guard of the pinned transport."""

import logging

import pytest
from curl_cffi import CurlHttpVersion, CurlOpt
from curl_cffi.requests import AsyncSession, Response

import rastro.adapters.http_options as http_options
from rastro.adapters.http import CurlTransport
from rastro.domain.acquisition import AcquisitionError, PinnedTarget, ProxyRoute

TARGET = PinnedTarget("https://shop.example/a", "shop.example", 443, "8.8.8.8")
PROXY = ProxyRoute("office", "http://proxy.example:8080")


def _capture(seen: dict, *, primary_ip: str = "8.8.8.8"):
    async def get(session, url, **kwargs):
        seen["options"] = dict(session.curl_options)
        seen.update(kwargs)
        result = Response()
        result.primary_ip = primary_ip
        result.status_code = 200
        return result

    return get


@pytest.fixture(autouse=True)
def fresh_warnings():
    http_options.reset_warnings()


@pytest.mark.asyncio
async def test_impersonate_keeps_resolve_pin(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    await CurlTransport().get(
        TARGET,
        headers={"User-Agent": "rastro", "Authorization": "Bearer token", "Accept": "text/html"},
        timeout=1,
        max_bytes=10,
        impersonate="chrome",
    )
    options = seen["options"]
    assert options[CurlOpt.RESOLVE] == [b"shop.example:443:8.8.8.8"]
    assert options[CurlOpt.PROXY] == b"" and options[CurlOpt.NOPROXY] == b"*"
    assert options[CurlOpt.FOLLOWLOCATION] == 0
    assert options[CurlOpt.MAXFILESIZE_LARGE] == 10
    assert options[CurlOpt.PROTOCOLS_STR] == b"http,https"
    assert seen["impersonate"].startswith("chrome")
    assert seen["default_headers"] is True
    assert not seen["allow_redirects"] and seen["verify"] and seen["discard_cookies"]
    # The browser's own User-Agent replaces Rastro's; the job's auth and Accept stay.
    assert "User-Agent" not in seen["headers"]
    assert seen["headers"]["Authorization"] == "Bearer token"
    assert seen["headers"]["Accept"] == "text/html"
    # Impersonation picks the browser's protocol unless the job names one.
    assert seen["http_version"] is None and CurlOpt.HTTP_VERSION not in options


@pytest.mark.asyncio
async def test_http3_option_passed(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    monkeypatch.setattr(http_options, "http3_available", lambda: True)
    await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10, http_version="3")
    options = seen["options"]
    assert seen["http_version"] == CurlHttpVersion.V3
    assert options[CurlOpt.HTTP_VERSION] == CurlHttpVersion.V3  # applied after impersonation
    assert options[CurlOpt.RESOLVE] == [b"shop.example:443:8.8.8.8"]
    assert seen["impersonate"] is None and seen["default_headers"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asked", "expected"),
    [(None, CurlHttpVersion.V1_1), ("1.1", CurlHttpVersion.V1_1), ("2", CurlHttpVersion.V2TLS)],
)
async def test_named_versions_map_to_curl(monkeypatch, asked, expected):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10, http_version=asked)
    assert seen["http_version"] == expected
    assert seen["options"][CurlOpt.HTTP_VERSION] == expected


@pytest.mark.asyncio
async def test_explicit_version_wins_over_the_browser_default(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    await CurlTransport().get(
        TARGET, headers={}, timeout=1, max_bytes=10, impersonate="firefox", http_version="1.1"
    )
    assert seen["impersonate"].startswith("firefox")
    assert seen["options"][CurlOpt.HTTP_VERSION] == CurlHttpVersion.V1_1


@pytest.mark.asyncio
async def test_destination_check_applies_with_impersonation_and_http3(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen, primary_ip="10.0.0.5"))
    monkeypatch.setattr(http_options, "http3_available", lambda: True)
    with pytest.raises(AcquisitionError, match=r"^destination_mismatch$"):
        await CurlTransport().get(
            TARGET, headers={}, timeout=1, max_bytes=10, impersonate="safari", http_version="3"
        )


@pytest.mark.asyncio
async def test_http3_through_a_proxy_falls_back_to_http2(monkeypatch, caplog):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    monkeypatch.setattr(http_options, "http3_available", lambda: True)
    with caplog.at_level(logging.WARNING, logger="rastro"):
        await CurlTransport().get(
            TARGET, headers={}, timeout=1, max_bytes=10, proxy=PROXY, http_version="3"
        )
    assert seen["options"][CurlOpt.HTTP_VERSION] == CurlHttpVersion.V2TLS
    assert seen["options"][CurlOpt.HTTPPROXYTUNNEL] == 1
    assert "http3_unavailable" in caplog.text and "proxy" in caplog.text
    assert "proxy.example" not in caplog.text


@pytest.mark.asyncio
async def test_http3_missing_from_libcurl_falls_back_with_a_warning(monkeypatch, caplog):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    monkeypatch.setattr(http_options, "http3_available", lambda: False)
    with caplog.at_level(logging.WARNING, logger="rastro"):
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10, http_version="3")
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10, http_version="3")
    assert seen["options"][CurlOpt.HTTP_VERSION] == CurlHttpVersion.V2TLS
    assert caplog.text.count("http3_unavailable") == 1  # once per process, not per request


@pytest.mark.asyncio
async def test_unknown_browser_target_falls_back_without_impersonation(monkeypatch, caplog):
    seen: dict = {}
    monkeypatch.setattr(AsyncSession, "get", _capture(seen))
    monkeypatch.setattr(http_options, "browser_target", lambda family: None)
    with caplog.at_level(logging.WARNING, logger="rastro"):
        await CurlTransport().get(
            TARGET, headers={"User-Agent": "rastro"}, timeout=1, max_bytes=10, impersonate="edge"
        )
    assert seen["impersonate"] is None and seen["default_headers"] is False
    assert seen["headers"]["User-Agent"] == "rastro"
    assert seen["options"][CurlOpt.HTTP_VERSION] == CurlHttpVersion.V1_1
    assert "impersonation_unavailable" in caplog.text and "edge" in caplog.text


def test_every_browser_family_has_an_installed_target():
    for family in ("chrome", "firefox", "safari", "edge"):
        assert http_options.browser_target(family)  # type: ignore[arg-type]


def test_http3_detection_reads_the_libcurl_features():
    assert isinstance(http_options.http3_available(), bool)
