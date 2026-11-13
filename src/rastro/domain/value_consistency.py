"""Deterministic value-consistency gate for relocated fields (REPAIR-002, REPAIR-004).

A relocated selector can match a different element whose text still validates,
for example a city where the title used to be. This gate compares the values a
candidate extracts for each relocated field with that field's values in the
retained success sample. It is a cheap shape check, not a semantic proof.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median

from pydantic import JsonValue

type Rows = Sequence[Mapping[str, JsonValue]]

TYPE_CHANGED = "value_type_changed"
SHAPE_CHANGED = "value_shape_changed"
OTHER_FIELD = "value_matches_other_field"
_PROFILE_TOLERANCE = 0.5  # max change in the share of digits, letters or other characters
_LENGTH_FACTOR = 3


def value_consistency(current: Rows, historical: Rows, fields: Sequence[str]) -> tuple[str, ...]:
    """Return the failed gate names for ``fields``; empty when every check passes.

    A field never observed in the sample is not checked. Checks, per field:
    the same JSON types; for strings a length within 3x of the historical median,
    a similar digit/letter/other profile and the same use of an http(s) scheme;
    and current values must not be drawn only from another field's history.
    """
    failed: list[str] = []
    for field in fields:
        past = _values(historical, field)
        now = _values(current, field)
        if not past or not now:
            continue
        if {_kind(v) for v in now} - {_kind(v) for v in past}:
            failed.append(TYPE_CHANGED)
        elif not _same_shape(now, past):
            failed.append(SHAPE_CHANGED)
        if _borrowed(now, past, field, historical):
            failed.append(OTHER_FIELD)
    return tuple(dict.fromkeys(failed))


def _values(rows: Rows, field: str) -> list[JsonValue]:
    return [row[field] for row in rows if row.get(field) is not None]


def _kind(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return type(value).__name__


def _same_shape(now: list[JsonValue], past: list[JsonValue]) -> bool:
    texts_now = [v for v in now if isinstance(v, str)]
    texts_past = [v for v in past if isinstance(v, str)]
    if not texts_now or not texts_past:
        return True
    middle = max(median(len(v) for v in texts_past), 1)
    if any(
        not middle / _LENGTH_FACTOR <= max(len(v), 1) <= middle * _LENGTH_FACTOR for v in texts_now
    ):
        return False
    if _has_scheme(texts_now) != _has_scheme(texts_past):
        return False
    before, after = _profile(texts_past), _profile(texts_now)
    return all(abs(a - b) <= _PROFILE_TOLERANCE for a, b in zip(before, after, strict=True))


def _has_scheme(texts: list[str]) -> bool:
    return all(v.lower().startswith(("http://", "https://")) for v in texts)


def _profile(texts: list[str]) -> tuple[float, float, float]:
    joined = "".join(texts)
    total = max(len(joined), 1)
    digits = sum(c.isdigit() for c in joined) / total
    letters = sum(c.isalpha() for c in joined) / total
    return digits, letters, 1 - digits - letters


def _borrowed(now: list[JsonValue], past: list[JsonValue], field: str, historical: Rows) -> bool:
    mine = {repr(v) for v in past}
    current = {repr(v) for v in now}
    if current & mine:
        return False
    others = {name for row in historical for name in row if name != field}
    return any(current <= {repr(v) for v in _values(historical, name)} for name in others)
