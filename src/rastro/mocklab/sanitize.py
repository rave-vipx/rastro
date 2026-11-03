"""Default-on, fail-closed sanitization of fixture artifacts (MOCK-005).

``sanitize_body`` rewrites an acquired HTML or JSON body according to a retention
plan; ``allowed_headers`` keeps only allowlisted response headers; ``assert_clean``
scans every finished artifact and refuses output that still matches a credential,
token or personal-data pattern. There is no switch that disables these steps.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from rastro.domain.json import strict_json
from rastro.domain.models import Representation
from rastro.mocklab.detectors import contains_secret, secret_categories
from rastro.mocklab.errors import SanitizationError
from rastro.mocklab.html_sanitizer import sanitize_html
from rastro.mocklab.retention import RetentionPlan, scrub_json
from rastro.mocklab.seeds import DeterministicStream

ALLOWED_HEADERS = frozenset({"content-language", "content-type"})
"""Response headers a fixture may record; cookies and authorization never qualify."""


def sanitize_body(body: str, representation: Representation, plan: RetentionPlan, seed: int) -> str:
    """Return the sanitized, unlabeled body; JSON output is canonical and indented."""
    stream = DeterministicStream(seed, "body")
    if representation == "html":
        return sanitize_html(body, plan, stream)
    try:
        payload = strict_json(body)
    except (ValueError, RecursionError):
        raise SanitizationError("JSON fixtures require a valid JSON body") from None
    scrubbed = scrub_json(payload, plan.json_pointers, stream)
    return json.dumps(scrubbed, ensure_ascii=False, indent=2) + "\n"


def allowed_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Keep allowlisted headers, lowercased and sorted; refuse any that carry secrets."""
    kept: dict[str, str] = {}
    for name, value in sorted((headers or {}).items(), key=lambda item: item[0].lower()):
        key = name.lower()
        if key not in ALLOWED_HEADERS:
            continue
        if contains_secret(value):
            raise SanitizationError(f"Response header {key!r} looks like sensitive data")
        kept[key] = value
    return kept


def assert_clean(files: Mapping[str, bytes]) -> None:
    """Raise ``SanitizationError`` naming the file and pattern, never the matched value."""
    for name, data in sorted(files.items()):
        found = secret_categories(data.decode())
        if found:
            raise SanitizationError(
                f"Generated file {name} still matches sensitive patterns: {', '.join(found)}"
            )
