"""Public curl_cffi transport: fresh pinned or proxied connections and bounded bodies."""

from __future__ import annotations

import ipaddress

from curl_cffi import CurlECode, CurlHttpVersion, CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from rastro.domain.acquisition import AcquisitionError, HttpResponse, PinnedTarget, ProxyRoute
from rastro.domain.models import Outcome

_PROXY_TYPES = {"http": 0, "https": 2}  # CURLPROXY_HTTP and CURLPROXY_HTTPS


class CurlTransport:
    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None = None,
    ) -> HttpResponse:
        body = bytearray()
        exceeded = False

        def receive(chunk: bytes) -> int:
            nonlocal exceeded
            if len(body) + len(chunk) > max_bytes:
                exceeded = True
                return (
                    CURL_WRITEFUNC_ERROR  # curl_cffi normalizes ordinary short writes to success.
                )
            body.extend(chunk)
            return len(chunk)

        address = f"[{target.address}]" if ":" in target.address else target.address
        options: dict[CurlOpt, object] = {
            CurlOpt.NETRC: 0,
            CurlOpt.FOLLOWLOCATION: 0,
            CurlOpt.PROTOCOLS_STR: b"http,https",
            CurlOpt.REDIR_PROTOCOLS_STR: b"http,https",
            CurlOpt.MAXFILESIZE_LARGE: max_bytes,
        }
        if proxy is None:
            options[CurlOpt.PROXY] = b""
            options[CurlOpt.NOPROXY] = b"*"
            # Literal addresses bypass DNS already; hostnames must use the validated record.
            try:
                ipaddress.ip_address(target.host)
            except ValueError:
                options[CurlOpt.RESOLVE] = [
                    f"{target.host}:{target.port}:{address}".encode("ascii")
                ]
        else:
            proxy_type = _PROXY_TYPES.get(proxy.url.split(":", 1)[0])
            if proxy_type is None or (proxy.password is not None and proxy.username is None):
                raise AcquisitionError("invalid_proxy_route", Outcome.PERMANENT_FAILURE)
            # The proxy resolves the target name, so a local RESOLVE pin cannot apply. The
            # tunnel keeps the proxy a byte relay; TLS still verifies the target hostname.
            options.update(
                {
                    CurlOpt.PROXY: proxy.url.encode("ascii"),
                    CurlOpt.PROXYTYPE: proxy_type,
                    CurlOpt.NOPROXY: b"",
                    CurlOpt.HTTPPROXYTUNNEL: 1,
                    CurlOpt.PROXY_CREDENTIAL_NO_REUSE: 1,
                }
            )
            if proxy.username is not None:
                options[CurlOpt.PROXYUSERNAME] = proxy.username.encode()
                options[CurlOpt.PROXYPASSWORD] = (proxy.password or "").encode()
        try:
            async with AsyncSession(
                trust_env=False, curl_options=options, max_clients=1, retry=0
            ) as session:
                response = await session.get(
                    target.url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                    max_redirects=0,
                    verify=True,
                    impersonate=None,
                    default_headers=False,
                    http_version=CurlHttpVersion.V1_1,
                    content_callback=receive,
                    discard_cookies=True,
                )
                if exceeded:
                    raise AcquisitionError("body_too_large", Outcome.PERMANENT_FAILURE)
                # Through a proxy, primary_ip is the proxy; the target was validated before.
                if (
                    proxy is None
                    and response.primary_ip
                    and ipaddress.ip_address(response.primary_ip)
                    != ipaddress.ip_address(target.address)
                ):
                    raise AcquisitionError("destination_mismatch", Outcome.ACCESS_RESTRICTION)
                selected_headers = {
                    key: response.headers[key]
                    for key in ("location", "retry-after", "content-type")
                    if key in response.headers
                }
                return HttpResponse(
                    status=response.status_code,
                    headers=selected_headers,
                    body=bytes(body),
                    bytes_received=int(response.download_size),
                )
        except RequestException as error:
            if exceeded or error.code == CurlECode.FILESIZE_EXCEEDED:
                raise AcquisitionError("body_too_large", Outcome.PERMANENT_FAILURE) from None
            if proxy is not None and error.code in {
                CurlECode.COULDNT_RESOLVE_PROXY,
                CurlECode.PROXY,
            }:
                raise AcquisitionError("proxy_failure", Outcome.TRANSIENT_FAILURE) from None
            if error.code == CurlECode.OPERATION_TIMEDOUT:
                raise AcquisitionError("request_timeout", Outcome.TRANSIENT_FAILURE) from None
            if error.code in {CurlECode.PEER_FAILED_VERIFICATION, CurlECode.SSL_CERTPROBLEM}:
                raise AcquisitionError(
                    "tls_verification_failed", Outcome.PERMANENT_FAILURE
                ) from None
            raise AcquisitionError("network_failure", Outcome.TRANSIENT_FAILURE) from None
