import asyncio

import pytest
from curl_cffi import CurlECode, CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import AsyncSession, Response
from curl_cffi.requests.exceptions import RequestException

from rastro.adapters.dns import SystemResolver
from rastro.adapters.http import CurlTransport
from rastro.domain.acquisition import AcquisitionError, PinnedTarget

pytestmark = pytest.mark.asyncio
TARGET = PinnedTarget("https://example.com/a?secret=value", "example.com", 443, "8.8.8.8")


async def test_transport_pins_destination_and_disables_ambient_behavior(monkeypatch):
    async def get(session, url, **kwargs):
        assert url == TARGET.url
        assert not session.trust_env
        assert session.curl_options[CurlOpt.RESOLVE] == [b"example.com:443:8.8.8.8"]
        assert session.curl_options[CurlOpt.PROXY] == b""
        assert session.curl_options[CurlOpt.NOPROXY] == b"*"
        assert session.curl_options[CurlOpt.NETRC] == 0
        assert session.curl_options[CurlOpt.MAXFILESIZE_LARGE] == 10
        assert session.curl_options[CurlOpt.FOLLOWLOCATION] == 0
        assert not kwargs["allow_redirects"]
        assert kwargs["verify"] and kwargs["discard_cookies"]
        assert kwargs["impersonate"] is None and not kwargs["default_headers"]
        assert kwargs["content_callback"](b"hello") == 5
        assert kwargs["content_callback"](b"world") == 5
        result = Response()
        result.primary_ip = "8.8.8.8"
        result.status_code = 200
        result.headers.update({"Content-Type": "text/html", "Set-Cookie": "secret"})
        result.download_size = 10
        return result

    monkeypatch.setattr(AsyncSession, "get", get)
    result = await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10)
    assert result.body == b"helloworld"
    assert result.headers == {"content-type": "text/html"}
    assert result.bytes_received == 10


@pytest.mark.parametrize("literal", [False, True])
async def test_ipv6_resolve_and_literal_urls(monkeypatch, literal):
    target = PinnedTarget(
        "https://[2606:4700:4700::1111]/",
        "2606:4700:4700::1111" if literal else "example.com",
        443,
        "2606:4700:4700::1111",
    )

    async def get(session, url, **kwargs):
        if literal:
            assert CurlOpt.RESOLVE not in session.curl_options
        else:
            assert session.curl_options[CurlOpt.RESOLVE] == [
                b"example.com:443:[2606:4700:4700::1111]"
            ]
        return Response()

    monkeypatch.setattr(AsyncSession, "get", get)
    await CurlTransport().get(target, headers={}, timeout=1, max_bytes=1)


async def test_decoded_body_limit_aborts_before_retaining_excess(monkeypatch):
    async def get(session, url, **kwargs):
        assert kwargs["content_callback"](b"ok") == 2
        assert kwargs["content_callback"](b"too much") == CURL_WRITEFUNC_ERROR
        raise RequestException("secret URL must never escape", code=CurlECode.WRITE_ERROR)

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError, match=r"^body_too_large$"):
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=3)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CurlECode.FILESIZE_EXCEEDED, "body_too_large"),
        (CurlECode.OPERATION_TIMEDOUT, "request_timeout"),
        (CurlECode.PEER_FAILED_VERIFICATION, "tls_verification_failed"),
        (CurlECode.SSL_CERTPROBLEM, "tls_verification_failed"),
        (CurlECode.COULDNT_CONNECT, "network_failure"),
    ],
)
async def test_transport_errors_never_expose_raw_messages(monkeypatch, error, code):
    async def get(session, url, **kwargs):
        raise RequestException("sensitive-data", code=error)

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError, match="^" + code + "$") as failure:
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10)
    assert failure.value.__suppress_context__


async def test_peer_mismatch_is_denied(monkeypatch):
    async def get(session, url, **kwargs):
        result = Response()
        result.primary_ip = "127.0.0.1"
        return result

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError, match="destination_mismatch"):
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=10)


async def test_system_resolver_success_and_safe_failure(monkeypatch):
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("8.8.8.8", 443))] * 2

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    assert await SystemResolver().resolve("example.com", 443) == ("8.8.8.8",)

    async def failure(*args, **kwargs):
        raise OSError("secret host")

    monkeypatch.setattr(loop, "getaddrinfo", failure)
    with pytest.raises(AcquisitionError, match=r"^dns_failure$"):
        await SystemResolver().resolve("example.com", 443)


async def test_body_limit_reaches_the_real_cffi_callback_bridge(monkeypatch):
    from types import SimpleNamespace

    from curl_cffi import ffi
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR, write_callback

    async def get(session, url, **kwargs):
        handle = ffi.new_handle(SimpleNamespace(callback=kwargs["content_callback"]))
        chunk = ffi.new("char[]", b"excess")
        # The bridge normalizes ordinary short writes to success. Only its explicit
        # error sentinel aborts libcurl; testing the Python callback alone misses it.
        assert write_callback(chunk, 1, 6, handle) == CURL_WRITEFUNC_ERROR
        raise RequestException("aborted", code=CurlECode.WRITE_ERROR)

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError, match="body_too_large"):
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=3)


async def test_truncated_body_cannot_become_success_if_transport_returns(monkeypatch):
    async def get(session, url, **kwargs):
        kwargs["content_callback"](b"excess")
        result = Response()
        result.status_code = 200
        return result

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError, match="body_too_large"):
        await CurlTransport().get(TARGET, headers={}, timeout=1, max_bytes=3)
