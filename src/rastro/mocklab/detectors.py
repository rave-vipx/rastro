"""Detect credential, token and personal-data patterns in text bound for artifacts (MOCK-005).

Detection is a second line of defense: the sanitizer replaces all unextracted text
anyway. It guards the values Mock Lab must keep (extracted fields, structural
attributes, URLs, headers, recipes) and scans every finished artifact.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from rastro.mocklab.seeds import DeterministicStream

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"),
    "bearer": re.compile(r"(?i)\b(?:bearer|basic|token)\s+[A-Za-z0-9._~+/-]{8,}=*"),
    "api_key": re.compile(
        r"\b(?:sk|pk|rk)[-_](?:live[-_]|test[-_])?[A-Za-z0-9]{16,}"
        r"|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}"
        r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35}"
    ),
    "credential_assignment": re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|passw(?:or)?d|session[_-]?id"
        r"|sid|token|cookie|authorization)\s*[\"']?\s*[=:]\s*[\"']?[^\s\"'&;,]{4,}"
    ),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "phone": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"
        r"|(?<![\w.])\+\d{1,3}(?:[\s-]?\d){8,13}(?![\w.])"
    ),
}

# Long unbroken identifier-like runs in URL paths are treated as opaque tokens.
_OPAQUE_SEGMENT = re.compile(r"[A-Za-z0-9_-]{24,}")
_SAFE_SCHEMES = frozenset({"http", "https"})


def secret_categories(text: str) -> tuple[str, ...]:
    """Names of the patterns found in ``text``; never the matched values."""
    return tuple(name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text))


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS.values())


def scrub_url(value: str, stream: DeterministicStream) -> str:
    """Keep scheme, host and path; drop credentials, query strings and fragments.

    Non-HTTP schemes (``javascript:``, ``mailto:``, ``data:``...) become ``#``, and path
    segments that look like tokens or personal data become placeholders.
    """
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        return "#"
    if parts.scheme and parts.scheme.lower() not in _SAFE_SCHEMES:
        return "#"
    segments = [
        stream.placeholder(segment)
        if contains_secret(segment) or _OPAQUE_SEGMENT.search(segment)
        else segment
        for segment in parts.path.split("/")
    ]
    netloc = f"{host}{port}" if parts.netloc else ""
    return urlunsplit((parts.scheme, netloc, "/".join(segments), "", ""))
