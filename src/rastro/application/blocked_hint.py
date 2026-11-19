"""Name the kind of block behind a refused source and point to the guide section for it.

A hint is built from Rastro's own issue code, the HTTP status and a marker read from the
refusal response (a header or a fixed phrase in its first bytes). Page text never leaves
this module: a hint holds a kind, a status number and a documentation anchor.
"""

from __future__ import annotations

from rastro.domain.acquisition import HttpResponse
from rastro.domain.source_notes import BlockedHint, BlockKind

__all__ = ["ANCHORS", "GUIDE", "blocked_hint", "response_marker"]

GUIDE = "docs/egress.md"
ANCHORS: dict[BlockKind, str] = {
    "cloud_ip_refused": "cloud-ip-refused",
    "login_wall": "login-wall",
    "captcha": "challenge",
    "challenge_unsolved": "challenge",
    "rate_limited": "rate-limited",
    "robots": "robots",
    "geo_blocked": "geo-blocked",
    "unknown": "blocked",
}
_REFUSALS = frozenset(
    {
        "access_restricted",
        "host_access_restricted",
        "rate_limited",
        "challenge_unsolved",
        "robots_denied",
    }
)
_REFUSAL_STATUSES = frozenset({401, 403, 429, 451, 503})
_SCAN_BYTES = 16_384
# Fixed phrases, matched lowercase. Cloud and hosting networks are refused with a network or
# autonomous-system ban page; challenges and captchas load their widget scripts.
_MARKERS: tuple[tuple[BlockKind, tuple[str, ...]], ...] = (
    ("challenge_unsolved", ("cf-chl-", "cf-turnstile", "challenge-platform")),
    ("captcha", ("g-recaptcha", "hcaptcha", "captcha-delivery")),
    (
        "cloud_ip_refused",
        (
            "error code: 1005",
            "error code: 1006",
            "autonomous system number",
            "datacenter ip",
            "data center ip",
            "hosting provider ip",
        ),
    ),
    ("login_wall", ('type="password"', "type='password'")),
)


def response_marker(response: HttpResponse) -> BlockKind | None:
    """The kind of refusal a 401, 403, 429, 451 or 503 response shows, if it shows one."""
    if response.status not in _REFUSAL_STATUSES:
        return None
    headers = {name.lower(): value for name, value in response.headers.items()}
    if headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return "challenge_unsolved"
    text = response.body[:_SCAN_BYTES].decode("latin-1").lower()
    for kind, phrases in _MARKERS:
        if any(phrase in text for phrase in phrases):
            return kind
    if "www-authenticate" in headers:
        return "login_wall"
    return None


def blocked_hint(
    issue: str | None, http_status: int | None, marker: BlockKind | None
) -> BlockedHint | None:
    """A hint for a refusal issue code; None for every other outcome."""
    if issue not in _REFUSALS:
        return None
    kind: BlockKind
    if issue == "robots_denied":
        kind, http_status = "robots", None
    elif issue == "challenge_unsolved":
        kind = "challenge_unsolved"
    elif marker is not None:
        kind = marker
    elif issue == "rate_limited" or http_status == 429:
        kind = "rate_limited"
    elif http_status == 401:
        kind = "login_wall"
    elif http_status == 451:
        kind = "geo_blocked"
    else:
        kind = "unknown"
    status = http_status if http_status is not None and 100 <= http_status <= 599 else None
    return BlockedHint(kind=kind, http_status=status, docs=f"{GUIDE}#{ANCHORS[kind]}")
