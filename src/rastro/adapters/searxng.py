"""Free self-hosted SearXNG JSON search adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from rastro.application.operator_trust import REMOTE_ENV, current_trust, env_flag
from rastro.domain.json import strict_json
from rastro.domain.models import ConfigurationError
from rastro.ports.search import SearchHit

if TYPE_CHECKING:
    import httpx

SEARXNG_ENV = "RASTRO_SEARXNG_URL"
_LOCAL_NAMES = frozenset({"localhost"})
_PATH = re.compile(r"(?:/(?!\.\.?(?:/|$))[A-Za-z0-9._~-]+)*")


def _load_httpx() -> Any:
    try:
        import httpx
    except ImportError:
        raise ConfigurationError(
            "SearXNG search needs httpx: pip install 'rastro-engine[openai]'"
        ) from None
    return httpx


def _validate_searxng_url(raw: str, *, allow_remote: bool) -> str:
    p = urlsplit(raw)
    h = p.hostname or ""
    if (
        p.scheme not in {"http", "https"}
        or not h
        or (p.username, p.password, p.query, p.fragment) != (None, None, "", "")
        or not _PATH.fullmatch(p.path.rstrip("/"))
    ):
        raise ConfigurationError("Use a credential-free HTTP(S) SearXNG origin")
    try:
        if p.port == 0:
            raise ValueError
    except ValueError:
        raise ConfigurationError("Use a credential-free HTTP(S) SearXNG origin") from None
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        addr = None
    loop = h.lower() in _LOCAL_NAMES or (
        addr is not None and (addr == ipaddress.ip_address("::1") or addr.is_loopback)
    )
    if not loop:
        if not (allow_remote or env_flag(REMOTE_ENV) or current_trust().allow_remote):
            raise ConfigurationError("Remote SearXNG requires allow_remote=True or --allow-remote")
        if p.scheme != "https":
            raise ConfigurationError("Remote SearXNG endpoints require HTTPS")
    host = f"[{addr}]" if addr is not None and addr.version == 6 else h
    path = p.path.rstrip("/")
    suffix = "" if path.endswith("/search") else "/search"
    return f"{p.scheme}://{host}{f':{p.port}' if p.port else ''}{path}{suffix}"


class SearXNGSearchProvider:
    """Query a self-hosted SearXNG JSON endpoint; no redirects, credentials or env proxies."""

    def __init__(self, base_url: str | None = None, **opts: Any) -> None:
        raw = (base_url if base_url is not None else os.environ.get(SEARXNG_ENV, "")).strip()
        if not raw:
            raise ConfigurationError(f"No SearXNG endpoint configured: set {SEARXNG_ENV}")
        self._url = _validate_searxng_url(raw, allow_remote=bool(opts.get("allow_remote", False)))
        self._httpx = _load_httpx()
        self._timeout = float(opts.get("timeout", 15.0))
        self._max_bytes = int(opts.get("max_response_bytes", 262_144))
        self._transport: httpx.AsyncBaseTransport | None = opts.get("transport")

    def __repr__(self) -> str:
        return f"SearXNGSearchProvider(endpoint={self._url!r})"

    async def search(self, query: str, limit: int = 10) -> tuple[SearchHit, ...]:
        q = query.strip()
        if not q or len(q) > 500 or not 1 <= limit <= 50:
            raise ConfigurationError("Search query must be 1..500 chars and limit 1..50")
        httpx = self._httpx
        hdrs = {"Accept": "application/json", "Accept-Encoding": "identity"}
        kw: dict[str, Any] = {
            "trust_env": False,
            "follow_redirects": False,
            "timeout": self._timeout,
        }
        try:
            async with (
                asyncio.timeout(self._timeout),
                httpx.AsyncClient(transport=self._transport, **kw) as client,
                client.stream(
                    "GET", self._url, params={"q": q, "format": "json"}, headers=hdrs
                ) as r,
            ):
                if r.status_code != 200:
                    raise ConfigurationError("SearXNG endpoint returned a non-200 status")
                if r.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ConfigurationError("SearXNG response must use identity encoding")
                body = bytearray(r.content if r.is_stream_consumed else b"")
                if not r.is_stream_consumed:
                    async for chunk in r.aiter_raw():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise ConfigurationError("SearXNG response exceeds max_response_bytes")
                elif len(body) > self._max_bytes:
                    raise ConfigurationError("SearXNG response exceeds max_response_bytes")
            payload = strict_json(bytes(body))
        except (TimeoutError, httpx.HTTPError):
            raise ConfigurationError("SearXNG search request failed") from None
        except (ValueError, RecursionError):
            raise ConfigurationError("SearXNG returned invalid JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ConfigurationError("SearXNG JSON response must contain a 'results' list")
        return tuple(
            SearchHit(
                it["url"].strip(), it["title"].strip() if isinstance(it.get("title"), str) else ""
            )
            for it in payload["results"]
            if isinstance(it, dict) and isinstance(it.get("url"), str) and it["url"].strip()
        )[:limit]
