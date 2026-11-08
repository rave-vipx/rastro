"""--upcoming and HTML link rules for dataset export (DATA-004, SCHED-007)."""

from datetime import UTC, datetime

import pytest

from rastro.cli_dataset_export import cell, select_records
from rastro.domain.dataset import StoredRecord
from rastro.domain.models import ConfigurationError


def _record(**fields: object) -> StoredRecord:
    return StoredRecord.model_construct(fields=fields)


def test_integers_are_not_dates():
    with pytest.raises(ConfigurationError):
        select_records((_record(num=20990101),), "num", True)


def test_starts_on_rows_survive_while_starts_at_none_is_dropped():
    rec = _record(starts_at=None, starts_on="2099-01-01")
    assert len(select_records((rec,), "starts_on", True)) == 1
    assert select_records((rec,), "starts_at", True) == ()


def test_date_compares_with_today_in_the_given_timezone():
    # 2026-11-09 01:00Z is still 2026-11-08 in Montevideo (UTC-3).
    now = datetime(2026, 11, 9, 1, tzinfo=UTC)
    rec = _record(starts_on="2026-11-08")
    assert select_records((rec,), "starts_on", True, timezone="America/Montevideo", now=now)
    assert not select_records((rec,), "starts_on", True, timezone="UTC", now=now)
    assert not select_records((rec,), "starts_on", True, now=now)


def test_unknown_timezone_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        select_records((_record(d="2099-01-01"),), "d", True, timezone="No/Such")


def test_links_use_case_insensitive_schemes_and_reject_whitespace():
    assert cell("Http://example.com/").startswith('<a href="Http://example.com/">')
    assert cell("https://example.com and prose") == "https://example.com and prose"
    assert cell("https://example.com/\tx") == "https://example.com/\tx"
