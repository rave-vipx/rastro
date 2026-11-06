"""SYNC-003: canonical identity preserves declared ordering and rejects ambiguity."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError


def test_identity_normalizes_unicode_whitespace_case_and_datetime():
    a = DatasetRecordKey.from_fields(
        "d",
        {
            "title": "  CAFE\u0301  ",
            "at": datetime(2026, 11, 6, 12, tzinfo=timezone(timedelta(hours=-3))),
        },
        ("title", "at"),
    )
    b = DatasetRecordKey.from_fields(
        "d", {"title": "café", "at": datetime(2026, 11, 6, 15, tzinfo=UTC)}, ("title", "at")
    )
    assert a == b
    assert a.identity == ("café", "2026-11-06T15:00:00.000000+00:00")


@pytest.mark.parametrize(
    "fields,names",
    [
        ({}, ("id",)),
        ({"id": " "}, ("id",)),
        ({"id": None}, ("id",)),
        ({"id": datetime(2026, 1, 1)}, ("id",)),
        ({"id": "x"}, ()),
    ],
)
def test_invalid_identity_has_typed_error(fields, names):
    with pytest.raises(DatasetStorageError):
        DatasetRecordKey.from_fields("d", fields, names)


def test_numeric_identity_is_distinct_from_text_and_boolean():
    keys = [DatasetRecordKey.from_fields("d", {"id": value}, ("id",)) for value in (1, "1", True)]
    assert len(set(keys)) == 3
    assert len({key.model_dump_json() for key in keys}) == 3


def test_datetime_text_offsets_normalize_idempotently():
    a = DatasetRecordKey.from_fields("d", {"id": "2026-11-06T12:00:00-03:00"}, ("id",))
    b = DatasetRecordKey.from_fields("d", {"id": "2026-11-06T15:00:00Z"}, ("id",))
    assert a == b == DatasetRecordKey.model_validate_json(a.model_dump_json())


def test_identity_string_is_not_silently_split_into_characters():
    with pytest.raises(DatasetStorageError):
        DatasetRecordKey.model_validate({"dataset": "d", "identity": "abc"})
