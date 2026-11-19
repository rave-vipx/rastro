"""Query-free diagnostic URLs and filtered, query-aware page identities."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["canonical_page_url", "provenance_url"]

# A key is secret-like when one of its words (split on separators and camelCase) is listed,
# or when its compact form ends in a secret suffix (``apikey``, ``jsessionid``).
_SECRET_WORDS = frozenset(
    {"token", "tokens", "key", "keys", "apikey", "sig", "signature", "session", "sessionid"}
    | {"sid", "auth", "authorization", "password", "passwd", "pwd", "pass", "secret", "secrets"}
    | {"credential", "credentials", "code", "state", "jwt", "csrf", "xsrf", "nonce", "otp"}
    | {"hmac", "ticket"}
)
_SECRET_SUFFIXES = (
    *("token", "secret", "password", "passwd", "apikey", "signature", "sessid", "sessionid"),
    *("credential", "credentials", "jwt", "csrf", "xsrf"),
)
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "mc_eid", "mc_cid", "yclid"}
    | {"twclid", "ttclid", "igshid", "_ga", "_gl", "_hsenc", "_hsmi"}
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def provenance_url(url: str) -> str:
    """``url`` reduced to scheme, host, port and path; never raises."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
    except ValueError:
        return ""
    path = "/".join(segment.partition(";")[0] for segment in parts.path.split("/"))
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def canonical_page_url(url: str) -> str:
    """Keep sorted page parameters, excluding secret-like keys and known tracking keys."""
    clean = provenance_url(url)
    try:
        parameters = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return clean
    kept = [(key, value) for key, value in parameters if not _dropped(key)]
    query = urlencode(sorted(kept))
    return f"{clean}?{query}" if query else clean


def _dropped(key: str) -> bool:
    """True for tracking keys and secret-like keys, whatever their case or separators."""
    folded = key.casefold()
    if folded.startswith("utm_") or folded in _TRACKING_KEYS:
        return True
    words = re.split(r"[^a-z0-9]+", _CAMEL.sub("_", key).casefold())
    compact = "".join(words)
    return compact.endswith(_SECRET_SUFFIXES) or not _SECRET_WORDS.isdisjoint(words)
