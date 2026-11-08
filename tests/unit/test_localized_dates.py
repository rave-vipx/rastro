"""Spanish source dates preserve explicit times (DATA-005)."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("America/Montevideo")
NOW = datetime(2026, 11, 7, 12, tzinfo=TZ)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 al 14 NOV 2026 - 20 H", datetime(2026, 11, 12, 20, tzinfo=TZ)),
        ("22 NOV 2026 - 19 H", datetime(2026, 11, 22, 19, tzinfo=TZ)),
        ("06 de noviembre", None),
        ("VIE 6 NOV 20:00 H", datetime(2026, 11, 6, 20, tzinfo=TZ)),
        ("Jueves 12 Nov", None),
        ("12, 13 y 14 de noviembre 20.30", datetime(2026, 11, 12, 20, 30, tzinfo=TZ)),
        ("LUNES 9 NOV 20:30 hs", datetime(2026, 11, 9, 20, 30, tzinfo=TZ)),
        ("9 nov 21 h", datetime(2026, 11, 9, 21, tzinfo=TZ)),
    ],
)
def test_parse_localized_real_pilot_strings(text, expected):
    from rastro.domain.localized_dates import parse_localized

    assert parse_localized(text, timezone=str(TZ), now=NOW, want_time=True) == expected
    expected_day = (
        expected.date() if expected else date(2026, 11, 6 if text.startswith("06") else 12)
    )
    assert parse_localized(text, timezone=str(TZ), now=NOW, want_time=False) == expected_day


def test_yearless_date_rolls_to_next_year():
    from rastro.domain.localized_dates import parse_localized

    assert parse_localized("5 ene", timezone=str(TZ), now=NOW, want_time=False) == date(2027, 1, 5)


@pytest.mark.parametrize("text", ["31 nov 20 H", "9 nov 25 H", "unknown", "0 nov", "9 nov 20:99"])
def test_invalid_dates_fail_explicitly(text):
    from rastro.domain.localized_dates import parse_localized

    assert parse_localized(text, timezone=str(TZ), now=NOW, want_time=True) is None


@pytest.mark.parametrize(
    ("month", "number"),
    [
        (name, index)
        for index, names in enumerate(
            [
                "ene enero",
                "feb febrero",
                "mar marzo",
                "abr abril",
                "may mayo",
                "jun junio",
                "jul julio",
                "ago agosto",
                "set sept septiembre",
                "oct octubre",
                "nov noviembre",
                "dic diciembre",
            ],
            start=1,
        )
        for name in names.split()
    ],
)
def test_spanish_months(month, number):
    from rastro.domain.localized_dates import parse_localized

    assert parse_localized(
        f"12 {month.upper()} 2026", timezone=str(TZ), now=NOW, want_time=False
    ) == date(2026, number, 12)


def test_rollover_uses_local_year_and_thirty_day_boundary():
    from rastro.domain.localized_dates import parse_localized

    assert parse_localized("8 oct", timezone=str(TZ), now=NOW, want_time=False) == date(2026, 10, 8)
    assert parse_localized("7 oct", timezone=str(TZ), now=NOW, want_time=False) == date(2027, 10, 7)
    utc_new_year = datetime.fromisoformat("2027-01-01T01:00:00Z")
    assert parse_localized("31 dic", timezone=str(TZ), now=utc_new_year, want_time=False) == date(
        2026, 12, 31
    )


@pytest.mark.parametrize("text", ["26 NOV 2026 - 19 H y 21.30 H", "26 NOV 2026 - 19 H, 21 H"])
def test_time_lists_use_the_first_time(text):
    from rastro.domain.localized_dates import parse_localized

    got = parse_localized(text, timezone=str(TZ), now=NOW, want_time=True)
    assert got == datetime(2026, 11, 26, 19, tzinfo=TZ)
    assert parse_localized(text, timezone=str(TZ), now=NOW, want_time=False) == date(2026, 11, 26)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-20T20:30:00-03:00", date(2026, 11, 20)),
        ("2026-11-21T01:30:00Z", date(2026, 11, 20)),
        ("2026-11-20", "2026-11-20"),
    ],
)
def test_localized_date_field_keeps_the_local_day_of_iso_datetimes(text, expected):
    from rastro.domain.job_field_values import localized_date

    info = SimpleNamespace(context={"timezone": str(TZ), "observed_at": NOW})
    assert localized_date(text, info, want_time=False) == expected  # type: ignore[arg-type]
