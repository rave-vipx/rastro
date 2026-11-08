"""Context-aware normalization before job field validation (DATA-005, JOB-002)."""

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationInfo

from rastro.domain.localized_dates import parse_localized


def localized_date(value: Any, info: ValidationInfo, *, want_time: bool) -> Any:
    if not isinstance(value, str):
        return value
    context = info.context or {}
    timezone, observed_at = context.get("timezone"), context.get("observed_at")
    try:
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
        has_time = re.search(r"[Tt ]\d{2}", value) is not None
        if want_time and not has_time:
            raise ValueError("datetime requires an explicit time")
        if not want_time and has_time and isinstance(timezone, str):
            return (iso.astimezone(ZoneInfo(timezone)) if iso.tzinfo else iso).date()
        return value
    except ZoneInfoNotFoundError:
        raise ValueError("unavailable date timezone") from None


def relative_url(value: Any, info: ValidationInfo) -> Any:
    source_url = (info.context or {}).get("source_url")
    if isinstance(value, str):
        if any(character.isspace() for character in value):
            raise ValueError("URL must not contain whitespace")
        relative = value.startswith(("./", "../")) or (
            value.startswith("/") and not value.startswith("//")
        )
        if source_url and relative:
            return urljoin(source_url, value)
    return value
