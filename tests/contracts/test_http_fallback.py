"""Connection metadata must prove no HTTP exchange began before fallback."""

import pytest
from curl_cffi import CurlECode, CurlInfo, CurlOpt
from curl_cffi.requests import AsyncSession, Response
from curl_cffi.requests.exceptions import RequestException

from rastro.adapters.http import CurlTransport
from rastro.domain.acquisition import AcquisitionError, PinnedTarget, ProxyRoute

pytestmark = pytest.mark.asyncio
TARGET = PinnedTarget("https://example.com/", "example.com", 443, "8.8.8.8")


@pytest.mark.parametrize(
    ("code", "connected", "status", "sent", "received", "proxy", "metadata", "expected"),
    [
        (CurlECode.OPERATION_TIMEDOUT, 0, 0, 0, 0, False, True, True),
        (CurlECode.COULDNT_CONNECT, 0, 0, 0, 0, False, True, True),
        (CurlECode.OPERATION_TIMEDOUT, 0.01, 0, 0, 0, False, True, False),
        (CurlECode.OPERATION_TIMEDOUT, 0, 503, 0, 0, False, True, False),
        (CurlECode.COULDNT_CONNECT, 0, 200, 0, 0, False, True, False),
        (CurlECode.OPERATION_TIMEDOUT, 0, 0, 10, 0, False, True, False),
        (CurlECode.OPERATION_TIMEDOUT, 0, 0, 0, 10, False, True, False),
        (CurlECode.RECV_ERROR, 0, 0, 0, 0, False, True, False),
        (CurlECode.OPERATION_TIMEDOUT, 0, 0, 0, 0, True, True, False),
        (CurlECode.OPERATION_TIMEDOUT, 0, 0, 0, 0, False, False, False),
        (CurlECode.OPERATION_TIMEDOUT, None, 0, 0, 0, False, True, False),
    ],
)
async def test_only_proven_connection_failures_allow_fallback(
    monkeypatch, code, connected, status, sent, received, proxy, metadata, expected
):
    async def get(session, url, **kwargs):
        assert CurlInfo.CONNECT_TIME in session.curl_infos
        result = Response()
        result.status_code = status
        result.request_size = sent
        result.header_size = received
        if connected is not None:
            result.infos[CurlInfo.CONNECT_TIME] = connected
        raise RequestException("sensitive input", code=code, response=result if metadata else None)

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(AcquisitionError) as failure:
        await CurlTransport().get(
            TARGET,
            headers={},
            timeout=1,
            max_bytes=10,
            proxy=ProxyRoute("office", "http://proxy.example") if proxy else None,
        )
    assert failure.value.connect_failure is expected
    assert str(failure.value) == (
        "request_timeout" if code == CurlECode.OPERATION_TIMEDOUT else "network_failure"
    )


async def test_connect_timeout_leaves_time_for_alternates(monkeypatch):
    target = PinnedTarget(TARGET.url, TARGET.host, TARGET.port, TARGET.address, ("1.1.1.1",))

    async def get(session, url, **kwargs):
        assert session.curl_options[CurlOpt.CONNECTTIMEOUT_MS] == 500
        assert kwargs["timeout"] == 1
        return Response()

    monkeypatch.setattr(AsyncSession, "get", get)
    await CurlTransport().get(target, headers={}, timeout=1, max_bytes=10)


@pytest.mark.parametrize("timeout", [0.0001, 0, -1])
async def test_transport_never_disables_curl_timeout(monkeypatch, timeout):
    async def get(session, url, **kwargs):
        assert int(kwargs["timeout"] * 1000) >= 1
        return Response()

    monkeypatch.setattr(AsyncSession, "get", get)
    if timeout > 0:
        await CurlTransport().get(TARGET, headers={}, timeout=timeout, max_bytes=10)
    else:
        with pytest.raises(AcquisitionError, match="request_timeout"):
            await CurlTransport().get(TARGET, headers={}, timeout=timeout, max_bytes=10)
