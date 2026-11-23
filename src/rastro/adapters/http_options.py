"""Map a job's HTTP options onto curl_cffi: browser targets and protocol versions (ADR 0038).

Browser families map to the newest target the installed curl_cffi ships, in this one place.
When the installed build lacks a requested target or HTTP/3, the transfer falls back to the
plain profile (or HTTP/2) and logs a warning with a stable event name once per process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache

import structlog
from curl_cffi import CurlHttpVersion
from curl_cffi.curl import Curl
from curl_cffi.requests.impersonate import REAL_TARGET_MAP, BrowserType

from rastro.domain.source_notes import BrowserFamily, HttpVersion

__all__ = ["TransferProfile", "browser_target", "http3_available", "profile", "reset_warnings"]

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_VERSIONS = {
    "1.1": CurlHttpVersion.V1_1,
    "2": CurlHttpVersion.V2TLS,  # never an h2c upgrade over cleartext
    "3": CurlHttpVersion.V3,  # QUIC first, TCP if the server does not answer it
}
_TARGETS = frozenset(item.value for item in BrowserType)
_warned: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class TransferProfile:
    """What one transfer asks curl for. ``version`` None lets the impersonated browser choose."""

    target: str | None
    version: CurlHttpVersion | None


def browser_target(family: BrowserFamily) -> str | None:
    """The installed curl_cffi target for ``family``, or None when this build lacks one."""
    target = REAL_TARGET_MAP.get(family)
    return target if target in _TARGETS else None


@cache
def http3_available() -> bool:
    """Whether the bundled libcurl was built with an HTTP/3 stack."""
    version = Curl().version()
    return any(stack in version for stack in (b"nghttp3", b"quiche", b"msh3"))


def reset_warnings() -> None:
    """Forget which fallbacks were logged (tests)."""
    _warned.clear()


def _warn_once(event: str, reason: str) -> None:
    if (event, reason) not in _warned:
        _warned.add((event, reason))
        _LOG.warning(event, reason=reason)


def profile(
    impersonate: BrowserFamily | None, http_version: HttpVersion | None, *, proxied: bool
) -> TransferProfile:
    """Resolve the job's options against the installed build and the egress route."""
    target = None
    if impersonate is not None:
        target = browser_target(impersonate)
        if target is None:
            _warn_once("impersonation_unavailable", impersonate)
    if http_version == "3" and (proxied or not http3_available()):
        # The CONNECT tunnel carries TCP only, so HTTP/3 cannot pass a proxy.
        _warn_once("http3_unavailable", "proxy" if proxied else "not_built")
        http_version = "2"
    if http_version is None:
        return TransferProfile(target, None if target else CurlHttpVersion.V1_1)
    return TransferProfile(target, _VERSIONS[http_version])
