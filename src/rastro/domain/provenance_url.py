"""Query-free diagnostic URLs and filtered, query-aware page identities."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["canonical_page_url", "page_identity", "provenance_url"]

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


def page_identity(url: str) -> str:
    """In-memory page identity: sorted query minus tracking keys only; never store it."""
    return _with_query(url, secrets=False)


def canonical_page_url(url: str) -> str:
    """Stored page URL: sorted query minus tracking keys and secret-like keys."""
    return _with_query(url, secrets=True)


def _with_query(url: str, *, secrets: bool) -> str:
    clean = provenance_url(url)
    try:
        parameters = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return clean
    kept = [
        (key, value)
        for key, value in parameters
        if not _tracking(key) and not (secrets and _secret_like(key))
    ]
    query = urlencode(sorted(kept))
    return f"{clean}?{query}" if query else clean


def _tracking(key: str) -> bool:
    folded = key.casefold()
    return folded.startswith("utm_") or folded in _TRACKING_KEYS


def _secret_like(key: str) -> bool:
    """Whatever the case or separators: ``access_token``, ``X-Amz-Signature``, ``apikey``."""
    words = re.split(r"[^a-z0-9]+", _CAMEL.sub("_", key).casefold())
    return "".join(words).endswith(_SECRET_SUFFIXES) or not _SECRET_WORDS.isdisjoint(words)
