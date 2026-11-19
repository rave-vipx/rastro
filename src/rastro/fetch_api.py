"""Fetch one URL under an acquisition policy, for app adapters (N15, SEC-001, SEC-003, SEC-008).

``rastro.fetch`` uses the same HTTP acquirer as ``rastro.scrape`` URL targets and jobs:
exact host allowlist, robots.txt, egress rules, request budgets, redirect checks and the
byte limit. Failures come back as an explicit ``Fetched`` outcome, never as raw errors
and never as a successful empty body.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import ValidationError

from rastro.application.robots_cache import robots_cache
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import (
    ConfigurationError,
    Issue,
    Limits,
    Outcome,
    Representation,
    Source,
)
from rastro.scrape_target import web_acquirer

_MEDIA_TYPES: dict[str, str] = {"html": "text/html", "json": "application/json"}


@dataclass(frozen=True)
class Fetched:
    """One fetch: ``body`` (also ``text``) is the decoded document, or None when ``status`` is not
    success.

    ``url`` is the final URL after any permitted redirect. ``media_type`` names the
    representation that was validated (``text/html`` or ``application/json``) and is None
    on failure. ``issues`` holds the safe failure category, such as ``robots_denied``.
    """

    status: Outcome
    url: str
    body: str | None
    media_type: str | None
    issues: tuple[Issue, ...] = ()

    @property
    def text(self) -> str | None:
        """The decoded document text (alias for ``body``), or None on failure."""
        return self.body


def fetch(
    url: str,
    *,
    policy: AcquisitionPolicy | None = None,
    representation: Representation = "html",
    max_bytes: int | None = None,
    locale: str = "en-US",
) -> Fetched:
    """Fetch ``url`` synchronously. See ``fetch_async``."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ConfigurationError(
            "rastro.fetch cannot run inside a running event loop; use await rastro.fetch_async(...)"
        )
    # The HTTP transport needs add_reader, so use the portable selector loop.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(
            fetch_async(
                url,
                policy=policy,
                representation=representation,
                max_bytes=max_bytes,
                locale=locale,
            )
        )


async def fetch_async(
    url: str,
    *,
    policy: AcquisitionPolicy | None = None,
    representation: Representation = "html",
    max_bytes: int | None = None,
    locale: str = "en-US",
) -> Fetched:
    """Fetch ``url`` under ``policy`` (default: only that URL's host, HTTPS unless the URL is
    ``http://``). ``max_bytes`` defaults to the ``Limits`` input bound for ``representation``.

    Raises ``ConfigurationError`` for an invalid URL, representation, byte bound or a
    browser render mode; every network, robots or policy refusal is a ``Fetched`` outcome.
    """
    if representation not in _MEDIA_TYPES:
        raise ConfigurationError("representation must be 'html' or 'json'")
    try:
        source = Source(url=url, locale=locale)
        limits = Limits() if max_bytes is None else _byte_limits(max_bytes)
        if policy is None:
            parts = urlsplit(str(source.url))
            policy = AcquisitionPolicy(
                allowed_hosts=(parts.hostname or "",), allow_http=parts.scheme == "http"
            )
    except ValidationError:
        raise ConfigurationError(
            "fetch needs an absolute http(s) URL without credentials and a valid max_bytes"
        ) from None
    if policy.render != "http":
        raise ConfigurationError("rastro.fetch supports render = 'http' only")
    with robots_cache():
        result = await web_acquirer(policy).acquire(
            source,
            max_bytes=limits.max_input_bytes(representation),
            representation=representation,
        )
    if result.status != Outcome.SUCCESS:
        code = result.issue_code or "acquisition_failed"
        return Fetched(result.status, result.url, None, None, (Issue(code=code),))
    if not result.body or not result.body.strip():
        # A blank document is never a successful fetch (same rule as the browser path).
        return Fetched(
            Outcome.INVALID_DATA, result.url, None, None, (Issue(code="empty_document"),)
        )
    return Fetched(Outcome.SUCCESS, result.url, result.body, _MEDIA_TYPES[representation])


def _byte_limits(max_bytes: int) -> Limits:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ConfigurationError("max_bytes must be an integer")
    return Limits(max_html_bytes=max_bytes, max_json_bytes=max_bytes)
