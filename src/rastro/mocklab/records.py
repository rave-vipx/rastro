"""Schema-valid synthetic records derived only from the model and a seed (MOCK-001).

Values are generated from field types and declared bounds. A field the generator
cannot satisfy (for example a ``pattern``) is resampled from values the fixture
already records as expected output, and is listed in ``resampled_fields``.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import NoneType, UnionType
from typing import Literal, Union, get_args, get_origin

from pydantic import AnyUrl, BaseModel, JsonValue, ValidationError
from pydantic.fields import FieldInfo

from rastro.domain.sources import Contract
from rastro.mocklab.errors import MockLabError
from rastro.mocklab.seeds import DeterministicStream

_UNSUPPORTED = object()
_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


class SyntheticRecords(Contract):
    records: tuple[dict[str, JsonValue], ...]
    resampled_fields: tuple[str, ...] = ()


def synthesize_records(
    schema: type[BaseModel],
    observed: Sequence[dict[str, JsonValue]],
    *,
    count: int,
    seed: int,
) -> SyntheticRecords:
    stream = DeterministicStream(seed, "records")
    records: list[dict[str, JsonValue]] = []
    resampled: set[str] = set()
    for _ in range(count):
        record, fields = _record(schema, observed, stream)
        records.append(record)
        resampled.update(fields)
    return SyntheticRecords(records=tuple(records), resampled_fields=tuple(sorted(resampled)))


def _record(
    schema: type[BaseModel], observed: Sequence[dict[str, JsonValue]], stream: DeterministicStream
) -> tuple[dict[str, JsonValue], set[str]]:
    draft = {name: _value(info, stream) for name, info in schema.model_fields.items()}
    resampled = {name for name, value in draft.items() if value is _UNSUPPORTED}
    _resample(draft, resampled, observed, stream)
    try:
        return _validated(schema, draft), resampled
    except ValidationError as error:
        failing = {str(item["loc"][0]) for item in error.errors() if item["loc"]} - resampled
    _resample(draft, failing, observed, stream)
    try:
        return _validated(schema, draft), resampled | failing
    except ValidationError:
        raise MockLabError("Cannot synthesize a schema-valid synthetic record") from None


def _resample(
    draft: dict[str, object],
    names: set[str],
    observed: Sequence[dict[str, JsonValue]],
    stream: DeterministicStream,
) -> None:
    for name in sorted(names):
        choices = [record[name] for record in observed if name in record]
        if not choices:
            raise MockLabError(f"Cannot synthesize a schema-valid value for field {name!r}")
        draft[name] = stream.choice(choices)


def _validated(schema: type[BaseModel], draft: dict[str, object]) -> dict[str, JsonValue]:
    dumped: dict[str, JsonValue] = schema.model_validate(draft).model_dump(mode="json")
    return dumped


def _bounds(info: FieldInfo, low: int, high: int) -> tuple[int, int]:
    for item in info.metadata:
        if (value := getattr(item, "ge", None)) is not None:
            low = max(low, int(value) + (value != int(value)))
        if (value := getattr(item, "gt", None)) is not None:
            low = max(low, int(value) + 1)
        if (value := getattr(item, "le", None)) is not None:
            high = min(high, int(value))
        if (value := getattr(item, "lt", None)) is not None:
            high = min(high, int(value) - 1 if value == int(value) else int(value))
    return low, high if high >= low else low


def _lengths(info: FieldInfo) -> tuple[int, int]:
    low, high = 1, 12
    for item in info.metadata:
        low = max(low, getattr(item, "min_length", None) or 0)
        if (value := getattr(item, "max_length", None)) is not None:
            high = min(high, value)
    return low, max(high, low)


def _value(info: FieldInfo, stream: DeterministicStream) -> object:
    kind = _required_type(info.annotation)
    if get_origin(kind) is Literal:
        return stream.choice(get_args(kind))
    if isinstance(kind, type) and issubclass(kind, enum.Enum):
        return stream.choice([member.value for member in kind])
    if kind is bool:
        return stream.integer(0, 1) == 1
    if kind in (int, float, Decimal):
        low, high = _bounds(info, 0, 1000)
        number = stream.integer(low, high)
        return number if kind is int else kind(number)
    if kind is str:
        return stream.letters(stream.integer(*_lengths(info)))
    if kind is date:
        return (_EPOCH + timedelta(days=stream.integer(0, 2000))).date()
    if kind is datetime:
        return _EPOCH + timedelta(seconds=stream.integer(0, 63_072_000))
    if isinstance(kind, type) and issubclass(kind, AnyUrl):
        return f"https://example.com/synthetic/{stream.letters(8)}"
    return _UNSUPPORTED


def _required_type(annotation: object) -> object:
    """Unwrap ``X | None``; optional fields still get a concrete synthetic value."""
    if get_origin(annotation) in (Union, UnionType):
        members = [item for item in get_args(annotation) if item is not NoneType]
        if len(members) == 1:
            return members[0]
    return annotation
