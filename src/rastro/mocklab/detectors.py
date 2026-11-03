"""Detect credential, token and personal-data patterns in text bound for artifacts (MOCK-005).

Detection is a second line of defense: the sanitizer replaces all unextracted text
and every attribute token the recipe does not name. Detectors guard the values Mock
Lab must keep (extracted fields, headers, the source URL) and scan every finished
artifact. Text is checked as written and after percent-decoding and NFKC
normalization, so ``%40`` and fullwidth at signs cannot hide an email address.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from urllib.parse import unquote, urlsplit, urlunsplit

from rastro.mocklab.seeds import DeterministicStream

_TOKEN = r"[A-Za-z0-9_-]"
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
    "url_secret_param": re.compile(
        r"(?i)[?&;][\w.-]*(?:key|token|secret|session|sid|sig|signature|auth|code|pass|pwd)"
        r"[\w.-]*=[^&#\s]+"
    ),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "phone": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"
        r"|(?<![\w.])\+\d{1,3}(?:[\s-]?\d){8,13}(?![\w.])"
    ),
    "ssn": re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
    "base64_credential": re.compile(
        r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4}){3,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)"
        r"(?![A-Za-z0-9+/=])"
    ),
    "hex_token": re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24,}(?![0-9A-Fa-f])"),
    "opaque_token": re.compile(
        rf"(?<!{_TOKEN})(?={_TOKEN}*\d)(?={_TOKEN}*[A-Za-z]){_TOKEN}{{20,}}(?!{_TOKEN})"
    ),
}
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
# A path segment mixing digits and letters this long is treated as an opaque identifier.
_OPAQUE_SEGMENT = re.compile(r"^(?=.*\d)(?=.*[A-Za-z]).{12,}$|^.{24,}$")
_SELECTOR_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_SAFE_SCHEMES = frozenset({"http", "https"})


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _has_card(text: str) -> bool:
    return any(_luhn(re.sub(r"\D", "", match.group())) for match in _CARD.finditer(text))


def _has_iban(text: str) -> bool:
    for match in _IBAN.finditer(text):
        compact = match.group().replace(" ", "")
        rotated = compact[4:] + compact[:4]
        if int("".join(str(int(char, 36)) for char in rotated)) % 97 == 1:
            return True
    return False


_CHECKED: dict[str, Callable[[str], bool]] = {"card": _has_card, "iban": _has_iban}


def normalize(text: str) -> str:
    """Percent-decode and NFKC-normalize so encoded or lookalike characters are seen."""
    return unicodedata.normalize("NFKC", unquote(text))


def secret_categories(text: str, *, include_opaque: bool = True) -> tuple[str, ...]:
    """Names of the detectors that match ``text``; never the matched values."""
    views = {text, normalize(text)}
    found = [
        name
        for name, pattern in SECRET_PATTERNS.items()
        if (include_opaque or name != "opaque_token") and any(map(pattern.search, views))
    ]
    found += [name for name, check in _CHECKED.items() if any(map(check, views))]
    return tuple(found)


def contains_secret(text: str, *, include_opaque: bool = True) -> bool:
    return bool(secret_categories(text, include_opaque=include_opaque))


def selector_tokens(text: str) -> frozenset[str]:
    """Identifier-like tokens of recipe selectors; only these may survive in attributes."""
    return frozenset(_SELECTOR_TOKEN.findall(text))


def sensitive_segment(segment: str) -> bool:
    """True for URL path segments that look like secrets, personal data or opaque IDs."""
    decoded = normalize(segment)
    return contains_secret(decoded) or _OPAQUE_SEGMENT.search(decoded) is not None


def scrub_url(value: str, stream: DeterministicStream, keep: frozenset[str] = frozenset()) -> str:
    """Keep scheme, host and recipe-named path segments; drop everything else.

    Credentials, query strings and fragments are removed, non-HTTP schemes
    (``javascript:``, ``mailto:``, ``data:``...) become ``#``, and every path segment
    the recipe's selectors do not name becomes a placeholder.
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
        segment
        if not segment or (segment in keep and not sensitive_segment(segment))
        else stream.placeholder(segment)
        for segment in parts.path.split("/")
    ]
    netloc = f"{host}{port}" if parts.netloc else ""
    return urlunsplit((parts.scheme, netloc, "/".join(segments), "", ""))
