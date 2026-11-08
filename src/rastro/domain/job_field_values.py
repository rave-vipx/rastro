"""Context-aware normalization before job field validation (DATA-005, JOB-002)."""

from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationInfo

from rastro.domain.localized_dates import parse_localized


def localized_date(value: Any, info: ValidationInfo, *, want_time: bool) -> Any:
    if not isinstance(value, str):
        return value
    context = info.context or {}
    timezone, observed_at = context.get("timezone"), context.get("observed_at")
    try:
        iso = datetime.fromisoformat(value)
    except ValueError:
        parsed = (
            parse_localized(value, timezone=timezone, now=observed_at, want_time=want_time)
            if isinstance(timezone, str) and isinstance(observed_at, datetime)
            else None
        )
        if parsed is None:
            raise ValueError("unparseable localized date") from None
        return parsed
    if not want_time and "T" in value.upper() and isinstance(timezone, str):
        # A date field keeps the local calendar day of an ISO datetime instead of rejecting it.
        return (iso.astimezone(ZoneInfo(timezone)) if iso.tzinfo else iso).date()
    return value


def relative_url(value: Any, info: ValidationInfo) -> Any:
    source_url = (info.context or {}).get("source_url")
    if isinstance(value, str) and source_url and not urlsplit(value).scheme:
        return urljoin(source_url, value)
    return value
