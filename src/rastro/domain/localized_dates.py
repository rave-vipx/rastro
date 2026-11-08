"""Deterministic Spanish date parsing without inventing missing times (DATA-005)."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_MONTHS = {
    name: month
    for month, names in enumerate(
        (
            "ene enero",
            "feb febrero",
            "mar marzo",
            "abr abril",
            "may mayo",
            "jun junio",
            "jul julio",
            "ago agosto",
            "set sept sep septiembre setiembre",
            "oct octubre",
            "nov noviembre",
            "dic diciembre",
        ),
        start=1,
    )
    for name in names.split()
}
_DATE = re.compile(
    r"(?:(?:lun(?:es)?|mar(?:tes)?|mi[eé](?:rcoles)?|jue(?:ves)?|vie(?:rnes)?|"
    r"s[aá]b(?:ado)?|dom(?:ingo)?)\.?\s+)?"
    r"(?P<day>\d{1,2})(?:(?:\s+al\s+|\s*[,y]\s*)\d{1,2})*"
    r"\s+(?:de\s+)?(?P<month>[a-z]+)\.?"
    r"(?:\s+(?:de\s+)?(?P<year>\d{4}))?"
    r"(?:\s*(?:-\s*)?(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?P<suffix>hs?)?)?",
    re.IGNORECASE,
)


def parse_localized(
    text: str, *, timezone: str, now: datetime, want_time: bool
) -> datetime | date | None:
    """Parse a Spanish calendar date; ranges/lists use their first day.

    Yearless dates use the observation's local year, advancing a year only
    when the date is more than 30 calendar days before the observation.
    """
    match = _DATE.fullmatch(text.strip().casefold())
    if match is None or (month := _MONTHS.get(match["month"])) is None:
        return None
    local_now = now.astimezone(ZoneInfo(timezone))
    try:
        day = date(int(match["year"] or local_now.year), month, int(match["day"]))
        if match["year"] is None and day < local_now.date() - timedelta(days=30):
            day = day.replace(year=day.year + 1)
        if not want_time:
            return day
        if match["hour"] is None or (match["minute"] is None and match["suffix"] is None):
            return None
        return datetime(
            day.year,
            day.month,
            day.day,
            int(match["hour"]),
            int(match["minute"] or 0),
            tzinfo=local_now.tzinfo,
        )
    except ValueError:
        return None
