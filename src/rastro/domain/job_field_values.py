"""Context-aware normalization before job field validation (DATA-005, JOB-002)."""

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError, ValidationInfo, ValidatorFunctionWrapHandler
from pydantic_core import PydanticCustomError, PydanticOmit

from rastro.domain.localized_dates import parse_localized
from rastro.domain.results import Issue


def strip_text(value: Any, *, required: bool) -> Any:
    """Strip surrounding whitespace (NBSP included) so constraints see the real value.

    A blank required value is a validation error. A blank optional value stays ``""`` so a
    present-but-blank element still fails ``min_length`` or type parsing instead of
    silently becoming absent.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if required and not stripped:
        raise ValueError("value is blank")
    return stripped


def localized_date(
    value: Any, info: ValidationInfo, *, want_time: bool, localized: bool = True
) -> Any:
    if isinstance(value, datetime) and want_time:
        return regional_datetime(value, info)
    if not isinstance(value, str):
        return value
    context = info.context or {}
    timezone, observed_at = context.get("timezone"), context.get("observed_at")
    try:
        try:
            iso = datetime.fromisoformat(value)
        except ValueError:
            if not localized:
                return value
            parsed = (
                parse_localized(value, timezone=timezone, now=observed_at, want_time=want_time)
                if isinstance(timezone, str) and isinstance(observed_at, datetime)
                else None
            )
            if parsed is None:
                raise ValueError("unparseable localized date") from None
            return parsed
        has_time = re.search(r"[Tt ]\d{2}", value) is not None
        if want_time and has_time:
            return regional_datetime(iso, info)
        if want_time and localized and not has_time:
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


def null_placeholder(value: Any, *, values: frozenset[str]) -> Any:
    if isinstance(value, str) and value.strip().casefold() in values:
        raise PydanticCustomError("null_placeholder", "Value is a null placeholder")
    return value


def regional_datetime(value: datetime, info: ValidationInfo) -> datetime:
    timezone = (info.context or {}).get("timezone")
    if value.tzinfo is not None or not isinstance(timezone, str):
        return value
    try:
        result = value.replace(tzinfo=ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("unavailable date timezone") from None
    field_issue(info, "naive_datetime_localized", info.field_name)
    return result


def field_issue(info: ValidationInfo, code: str, name: str | None) -> bool:
    if not isinstance(info.context, dict):
        return False
    info.context.setdefault("field_issues", []).append(Issue(code=code, field=name))
    return True


def recover_field(
    value: Any,
    handler: ValidatorFunctionWrapHandler,
    info: ValidationInfo,
    *,
    code: str,
    name: str,
) -> Any:
    try:
        return handler(value)
    except ValidationError:
        if not field_issue(info, code, name):
            raise
        if code == "list_item_dropped":
            raise PydanticOmit from None
        return None


def bounded_list(value: Any, *, maximum: int) -> Any:
    if not isinstance(value, list):
        raise PydanticCustomError("list_type", "Input should be a valid list")
    if len(value) > maximum:
        raise PydanticCustomError("too_long", "List exceeds the maximum number of items")
    return value
