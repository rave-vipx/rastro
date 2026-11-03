"""Pure HTTP request, redirect, status, encoding, egress, and Retry-After rules."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    HttpResponse,
    ProxyRoute,
)
from rastro.domain.models import LANGUAGE_TAG, Outcome, Representation, Source

USER_AGENT = "Rastro/0.1 (+https://github.com/rave-vipx/rastro)"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
TRANSIENT_STATUSES = frozenset({408, 425, 500, 502, 503, 504})


def retry_after(value: str | None) -> float | None:
    """Parse delta seconds or an HTTP date without shortening a server's delay."""
    if value is None:
        return None
    if re.fullmatch(r"[0-9]+", value.strip()):
        return min(float(value), sys.float_info.max)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def resolve_proxy(
    egress: str, policy: AcquisitionPolicy, environ: Mapping[str, str] | None
) -> ProxyRoute | None:
    """Resolve one named profile before DNS. There is no fallback to another egress."""
    if egress == "direct":
        return None
    profile = policy.egress_profiles.get(egress)
    if profile is None:
        raise AcquisitionError("unsupported_egress", Outcome.PERMANENT_FAILURE)
    env = os.environ if environ is None else environ
    credentials: list[str | None] = []
    for name in (profile.username_env, profile.password_env):
        value = None if name is None else env.get(name)
        if name is not None and (
            not value or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AcquisitionError("egress_credentials_unavailable", Outcome.PERMANENT_FAILURE)
        credentials.append(value)
    return ProxyRoute(egress, profile.proxy_url, *credentials)


def request_headers(source: Source, representation: Representation) -> dict[str, str]:
    accept = "application/json" if representation == "json" else "text/html"
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    languages = source.languages or (() if source.locale == "und" else (source.locale,))
    if len(languages) > 20 or any(
        re.fullmatch(LANGUAGE_TAG, language) is None for language in languages
    ):
        raise AcquisitionError("invalid_locale", Outcome.PERMANENT_FAILURE)
    if languages:
        headers["Accept-Language"] = ", ".join(
            language if index == 0 else f"{language};q={(20 - index) / 20:g}"
            for index, language in enumerate(languages)
        )
    return headers


def redirect_target(url: str, response: HttpResponse, policy: AcquisitionPolicy) -> str:
    location = response.headers.get("location")
    if not location:
        raise AcquisitionError("invalid_redirect", Outcome.PERMANENT_FAILURE)
    try:
        joined = urljoin(url, location)
    except ValueError:
        raise AcquisitionError("invalid_redirect", Outcome.PERMANENT_FAILURE) from None
    target, _, _ = permitted_url(joined, policy)
    if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
        raise AcquisitionError("https_downgrade", Outcome.ACCESS_RESTRICTION)
    return target


def check_status(status: int) -> None:
    if status == 200:
        return
    if status in {401, 403, 429, 451}:
        raise AcquisitionError("access_restricted", Outcome.ACCESS_RESTRICTION)
    if status in TRANSIENT_STATUSES or status >= 500:
        raise AcquisitionError("http_transient_failure", Outcome.TRANSIENT_FAILURE)
    raise AcquisitionError("http_permanent_failure", Outcome.PERMANENT_FAILURE)


def decode_body(response: HttpResponse, representation: Representation = "html") -> str:
    media = Message()
    media["content-type"] = response.headers.get("content-type", "")
    content_type = media.get_content_type()
    if representation == "json":
        # RFC 8259: JSON exchanged between systems is UTF-8; other charsets are invalid.
        if content_type != "application/json" and not (
            content_type.startswith("application/") and content_type.endswith("+json")
        ):
            raise AcquisitionError("unsupported_content_type", Outcome.PERMANENT_FAILURE)
        if (media.get_content_charset() or "utf-8") not in {"utf-8", "utf8"}:
            raise AcquisitionError("invalid_encoding", Outcome.INVALID_DATA)
    elif content_type not in {"text/html", "application/xhtml+xml"}:
        raise AcquisitionError("unsupported_content_type", Outcome.PERMANENT_FAILURE)
    try:
        return response.body.decode(media.get_content_charset() or "utf-8", errors="strict")
    except (LookupError, UnicodeError):
        raise AcquisitionError("invalid_encoding", Outcome.INVALID_DATA) from None
