"""Schedules written in words: parse, describe, next runs, and the time of day in the scheduler."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError
from rastro.domain.schedule import ScheduleSpec, due_sources
from rastro.domain.schedule_words import describe_schedule, next_runs, parse_schedule_words

CASES = [
    ("every day at 8:00", "en", "1d", "08:00"),
    ("todos los días a las 8:00", "es", "1d", "08:00"),
    ("every 6 hours", "en", "6h", None),
    ("cada 6 horas", "es", "6h", None),
    ("every hour", "en", "1h", None),
    ("cada hora", "es", "1h", None),
    ("every 2 days at 14:30", "en", "2d", "14:30"),
    ("cada 2 días a las 14:30", "es", "2d", "14:30"),
]


@pytest.mark.parametrize(("words", "lang", "every", "at"), CASES)
def test_schedule_words_parse(words, lang, every, at):
    spec = parse_schedule_words(words)
    assert (spec.every, spec.at) == (every, at)
    assert describe_schedule(spec, lang) == words


def test_schedule_words_accept_spanish_without_accents_and_case():
    assert parse_schedule_words("Todos los dias a las 8:00") == parse_schedule_words(
        "todos los días a las 8:00"
    )


def test_next_three_run_times_follow_time_of_day():
    spec = parse_schedule_words("every day at 8:00")
    now = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    assert next_runs(spec, now) == [datetime(2030, 1, d, 8, 0, tzinfo=UTC) for d in (2, 3, 4)]
    local = next_runs(spec, now, timezone="Etc/GMT-2")  # UTC+2
    assert local[0] == datetime(2030, 1, 2, 6, 0, tzinfo=UTC)


def test_next_runs_for_plain_interval():
    spec = parse_schedule_words("every 6 hours")
    now = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    assert next_runs(spec, now) == [now + timedelta(hours=6 * i) for i in (1, 2, 3)]


def test_unparseable_schedule_names_closest():
    with pytest.raises(ConfigurationError) as caught:
        parse_schedule_words("evry day at 8:00")
    assert "every day at 8:00" in str(caught.value.hint)
    with pytest.raises(ConfigurationError):
        parse_schedule_words("every day at 25:00")
    with pytest.raises(ConfigurationError):
        parse_schedule_words("every 6 hours at 8:00")  # time of day needs whole days


def test_spec_with_time_of_day_is_validated():
    with pytest.raises(ConfigurationError):
        ScheduleSpec.model_validate({"every": "6h", "at": "08:00"})
    with pytest.raises(ConfigurationError):
        ScheduleSpec.model_validate({"every": "1d", "at": "8am"})
    assert ScheduleSpec.model_validate({"every": "1d", "at": "08:00"}).at == "08:00"


def test_scheduler_waits_for_the_time_of_day():
    from rastro.domain.dataset import SourceStatus
    from tests.unit.test_schedule import BASE_JOB

    raw = {**BASE_JOB, "schedule": {"every": "1d", "at": "08:00"}}
    raw["sources"] = raw["sources"][:1]
    job = parse_job(json.dumps(raw), format="json")
    last = datetime(2030, 1, 1, 8, 0, 5, tzinfo=UTC)
    status = SourceStatus(
        source_url="https://a.example/items", last_success=last, last_attempt=last, stale=False
    )
    early = datetime(2030, 1, 2, 7, 59, tzinfo=UTC)
    on_time = datetime(2030, 1, 2, 8, 0, tzinfo=UTC)
    assert [s.url for s in due_sources(job, [status], early)] == []
    assert [s.url for s in due_sources(job, [status], on_time)] == ["https://a.example/items"]
