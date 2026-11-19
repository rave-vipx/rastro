"""Browser network flags and the per-request target check shared by every browser engine.

The guard judges each request by its own URL (the target), never by the proxy hop: the
allowlist and address rules apply to the page and its resources wherever they are routed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, PinnedTarget, ProxyRoute

__all__ = ["browser_flags", "launch_options", "proxy_option", "resolver_rules", "route_allowed"]


def route_allowed(url: str, resource_type: str, policy: AcquisitionPolicy) -> bool:
    if resource_type.lower() in {"image", "media", "font", "websocket"}:
        return False
    try:
        permitted_url(url, policy)
        return True
    except AcquisitionError:
        return False


def resolver_rules(targets: tuple[PinnedTarget, ...], proxy: ProxyRoute | None = None) -> str:
    """Pinned targets only; the operator's proxy host is the one name resolved normally."""
    proxy_host = urlsplit(proxy.url).hostname if proxy is not None else None
    return ", ".join(
        [
            *(f"MAP {t.host} {t.address}" for t in targets),
            "MAP * ~NOTFOUND",
            "EXCLUDE localhost",
            *([f"EXCLUDE {proxy_host}"] if proxy_host else []),
        ]
    )


def browser_flags(targets: tuple[PinnedTarget, ...], proxy: ProxyRoute | None = None) -> list[str]:
    """Through a proxy, the proxy resolves target names: the guard still checks every request
    URL and the target was pinned and checked before the browser started."""
    return [
        f"--host-resolver-rules={resolver_rules(targets, proxy)}",
        "--disable-quic",
        *([] if proxy is not None else ["--no-proxy-server"]),
        "--disable-background-networking",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]


def proxy_option(proxy: ProxyRoute | None) -> dict[str, str] | None:
    """The route in the browser's proxy form; the credentials go to the proxy only."""
    if proxy is None:
        return None
    option = {"server": proxy.url}
    if proxy.username is not None:
        option["username"] = proxy.username
        option["password"] = proxy.password or ""
    return option


def launch_options(targets: tuple[PinnedTarget, ...], proxy: ProxyRoute | None) -> dict[str, Any]:
    """Chromium launch arguments: the flags, plus the proxy when the render uses one."""
    option = proxy_option(proxy)
    return {"args": browser_flags(targets, proxy), **({"proxy": option} if option else {})}
