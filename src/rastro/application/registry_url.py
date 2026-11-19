"""A hosted registry is an https URL that serves the Git-directory layout (ADR 0033).

The URL is operator input, never a grant: no userinfo, no query, no fragment, and a path of
plain segments. A non-default port or a private address still needs the operator's flags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.acquisition_base import exact_host
from rastro.domain.registry import RegistryError

DEFAULT_HUB_URL = "https://raw.githubusercontent.com/rave-vipx/rastro-hub/main/"
"""The public recipe hub: a static Git repository served raw, with the registry layout."""

_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_MESSAGE = "Registry URL must be https://HOST[:PORT][/PATH] without credentials, query or fragment"


@dataclass(frozen=True)
class RegistryLocation:
    """``base`` always ends in ``/``; files are fetched as ``base + relative path``."""

    base: str
    host: str


def parse_registry_url(url: str) -> RegistryLocation:
    """Validate a registry URL; failures never echo any part of it."""
    if not isinstance(url, str) or len(url) > 2048 or not url.isprintable() or " " in url:
        raise RegistryError(_MESSAGE)
    if any(mark in url for mark in ("?", "#", "@", "\\", "%")):
        raise RegistryError(_MESSAGE)
    try:
        parts = urlsplit(url)
        port = parts.port
        host = exact_host(parts.hostname or "")
    except ValueError:
        raise RegistryError(_MESSAGE) from None
    segments = parts.path.strip("/").split("/") if parts.path.strip("/") else []
    if (
        parts.scheme != "https"
        or not host
        or port == 0
        or parts.netloc.endswith(":")
        or any(not _SEGMENT.fullmatch(item) or item in {".", ".."} for item in segments)
    ):
        raise RegistryError(_MESSAGE)
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return RegistryLocation(f"https://{netloc}/" + "".join(f"{item}/" for item in segments), host)


def registry_policy(location: RegistryLocation) -> AcquisitionPolicy:
    """Only the registry host, https only, a handful of requests, same-host redirects."""
    return AcquisitionPolicy(
        allowed_hosts=(location.host,),
        max_requests=16,
        max_redirects=3,
        max_retries=1,
        min_interval_seconds=0,
    )
