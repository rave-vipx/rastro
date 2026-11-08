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
    r"(?P<days>\d{1,2}(?:(?:\s+al\s+|\s*[,y]\s*)\d{1,2})*)"
    r"\s+(?:de\s+)?(?P<month>[a-z]+)\.?"
    r"(?:\s+(?:de\s+)?(?P<year>\d{4}))?"
    r"(?:\s*(?:-\s*)?(?P<times>\d{1,2}(?:[:.]\d{2})?\s*hs?"
    r"|\d{1,2}[:.]\d{2})"
    r"(?P<more_times>(?:\s*(?:y|,)\s*\d{1,2}(?:[:.]\d{2})?\s*hs?)*))?",
    re.IGNORECASE,
)


def parse_localized(
    text: str, *, timezone: str, now: datetime, want_time: bool
) -> datetime | date | None:
    """Parse a Spanish calendar date; ranges and lists use their first day and first time.

    Yearless dates choose the earliest valid adjacent-year date within the
    30-day lookback or in the future; every listed day and time must be valid.
    """
    match = _DATE.fullmatch(text.strip().casefold())
    if match is None or (month := _MONTHS.get(match["month"])) is None:
        return None
    local_now = now.astimezone(ZoneInfo(timezone))
    day = _calendar_day(match, month, local_now.date())
    if day is None:
        return None
    times = re.split(r"\s*(?:y|,)\s*", (match["times"] or "") + (match["more_times"] or ""))
    parsed_times = []
    try:
        for text_time in filter(None, times):
            parts = re.findall(r"\d+", text_time)
            parsed_times.append(
                datetime(
                    day.year,
                    day.month,
                    day.day,
                    int(parts[0]),
                    int(parts[1]) if len(parts) > 1 else 0,
                    tzinfo=local_now.tzinfo,
                )
            )
    except ValueError:
        return None
    return (parsed_times[0] if parsed_times else None) if want_time else day


def _calendar_day(match: re.Match[str], month: int, today: date) -> date | None:
    days = [int(value) for value in re.findall(r"\d+", match["days"])]
    years = [int(match["year"])] if match["year"] else range(today.year - 1, today.year + 2)
    for year in years:
        try:
            candidates = [date(year, month, day) for day in days]
        except ValueError:
            continue
        if match["year"] or candidates[0] >= today - timedelta(days=30):
            return candidates[0]
    return None
