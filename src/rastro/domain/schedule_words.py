"""Schedules in plain words ("every day at 8:00", "todos los días a las 8:00") and back."""

from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from rastro.domain.models import ConfigurationError
from rastro.domain.schedule import ScheduleSpec

_UNITS = {
    "minute": "m", "minutes": "m", "minuto": "m", "minutos": "m",
    "hour": "h", "hours": "h", "hora": "h", "horas": "h",
    "day": "d", "days": "d", "dia": "d", "dias": "d",
}  # fmt: skip
_AT = r"(?: (?:at|a las|a la) (?P<h>[0-9]{1,2})(?::(?P<m>[0-9]{2}))?)?"
_PATTERNS = (
    re.compile(rf"every (?:(?P<n>[0-9]+) )?(?P<u>[a-z]+){_AT}"),
    re.compile(rf"cada (?:(?P<n>[0-9]+) )?(?P<u>[a-z]+){_AT}"),
    re.compile(rf"todos los (?P<u>dias){_AT}"),
)
_NAMES = {
    "en": {"every": "every", "m": ("minute", "minutes"), "h": ("hour", "hours"),
           "d": ("day", "days"), "at": "at"},
    "es": {"every": "cada", "m": ("minuto", "minutos"), "h": ("hora", "horas"),
           "d": ("día", "días"), "at": "a las"},
}  # fmt: skip


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFD", text.strip().lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def _split(delta: timedelta) -> tuple[int, str]:
    seconds = int(delta.total_seconds())
    for unit, size in (("d", 86_400), ("h", 3_600), ("m", 60)):
        if seconds % size == 0:
            return seconds // size, unit
    return seconds // 60, "m"


def describe_schedule(spec: ScheduleSpec, language: str = "en") -> str:
    """The spec in words, in English (`en`) or Spanish (`es`); `parse_schedule_words` reads it."""
    names = _NAMES.get(language)
    if names is None:
        raise ConfigurationError("Language must be en or es", location="language")
    count, unit = _split(spec.every_delta)
    singular, plural = names[unit]
    if language == "es" and unit == "d" and count == 1:
        words = "todos los días"
    elif count == 1:
        words = f"{names['every']} {singular}"
    else:
        words = f"{names['every']} {count} {plural}"
    if spec.at is not None:
        hour, minute = spec.at.split(":")
        words += f" {names['at']} {int(hour)}:{minute}"
    return words


def _candidates() -> list[str]:
    out = []
    for language in _NAMES:
        for every in ("1m", "5m", "30m", "1h", "6h", "1d", "2d"):
            out.append(describe_schedule(ScheduleSpec(every=every), language))
        out.append(describe_schedule(ScheduleSpec(every="1d", at="08:00"), language))
    return out


def parse_schedule_words(text: str) -> ScheduleSpec:
    """Read "every 6 hours" or "todos los días a las 8:00" into a spec; name the closest form."""
    plain = _plain(text)
    for pattern in _PATTERNS:
        match = pattern.fullmatch(plain)
        unit = _UNITS.get(match["u"]) if match else None
        if match is None or unit is None:
            continue
        at = None
        if match["h"] is not None:
            if unit != "d" or int(match["h"]) > 23 or int(match["m"] or 0) > 59:
                break
            at = f"{int(match['h']):02d}:{match['m'] or '00'}"
        data = {"every": f"{int(match.groupdict().get('n') or 1)}{unit}"}
        return ScheduleSpec.model_validate({**data, **({"at": at} if at else {})})
    close = difflib.get_close_matches(plain, _candidates(), n=1, cutoff=0.5)
    hint = f'Closest form: "{close[0]}"' if close else 'Write it like "every day at 8:00"'
    raise ConfigurationError("Schedule words are not understood", location="schedule", hint=hint)


def _at_run(day: object, spec_at: str, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.fromisoformat(spec_at), zone)  # type: ignore[arg-type]


def next_runs(
    spec: ScheduleSpec, after: datetime, count: int = 3, *, timezone: str = "UTC"
) -> list[datetime]:
    """The next `count` run times after `after` (which must be aware); the time of day is read
    in `timezone`."""
    if after.tzinfo is None:
        raise ConfigurationError("Run times need an aware start", location="after")
    if spec.at is None:
        return [after + spec.every_delta * i for i in range(1, count + 1)]
    zone, step = ZoneInfo(timezone), spec.every_delta.days
    run = _at_run(after.astimezone(zone).date(), spec.at, zone)
    if run <= after:
        run = _at_run(run.date() + timedelta(days=1), spec.at, zone)
    out = [run]
    while len(out) < count:
        out.append(_at_run(out[-1].date() + timedelta(days=step), spec.at, zone))
    return [moment.astimezone(after.tzinfo) for moment in out]


def next_due_at(spec: ScheduleSpec, last_attempt: datetime, timezone: str = "UTC") -> datetime:
    """When a source last tried at `last_attempt` is due again."""
    if spec.at is None:
        return last_attempt + spec.every_delta
    zone = ZoneInfo(timezone)
    day = last_attempt.astimezone(zone).date() + timedelta(days=spec.every_delta.days - 1)
    run = _at_run(day, spec.at, zone)
    if run <= last_attempt:
        run = _at_run(run.date() + timedelta(days=1), spec.at, zone)
    return run.astimezone(last_attempt.tzinfo)
