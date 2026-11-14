"""Content-free outcome detail of a failed source (DATA-005, SCR-002, OPS).

Only Rastro's own issue codes and HTTP status numbers are used, never page content.
"""

import re
from typing import Any

from rastro.domain.models import ExtractionResult, Outcome

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TIMEOUTS = {"request_timeout", "operation_timeout"}
_DENIED_STATUSES = {401, 403, 451}


def failure_detail(result: ExtractionResult[Any]) -> str | None:
    """``robots_denied``, ``http_429``, ``http_403`` (401, 451), ``timeout`` or an issue code."""
    if result.status in (Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS):
        return None
    codes = [issue.code for issue in result.issues]
    if "robots_denied" in codes:
        return "robots_denied"
    if "rate_limited" in codes or result.http_status == 429:
        return "http_429"
    if result.http_status in _DENIED_STATUSES:
        return f"http_{result.http_status}"
    if _TIMEOUTS.intersection(codes):
        return "timeout"
    return next((code for code in codes if _CODE.fullmatch(code)), None)
