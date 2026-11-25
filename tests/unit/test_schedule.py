"""Unit tests for job scheduling, due calculation, jitter and locks."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rastro.application.errors import RunInProgressError
from rastro.application.run_lock import (
    checkpoint_path,
    clear_checkpoint,
    dataset_lock,
    lock_path,
    read_checkpoint,
    write_checkpoint,
)
from rastro.domain.dataset import SourceStatus
from rastro.domain.jobs import Job, parse_job
from rastro.domain.models import ConfigurationError
from rastro.domain.schedule import (
    ScheduleSpec,
    deterministic_jitter,
    due_sources,
    next_sleep_seconds,
    parse_duration,
    source_every,
)

BASE_JOB: dict[str, Any] = {
    "format_version": 1,
    "recipe": {"strategy": "css", "record_selector": ".item", "fields": {"title": "h2::text"}},
    "sources": [
        {"path": "a.html", "url": "https://a.example/items"},
        {"path": "b.html", "url": "https://b.example/items", "every": "1h"},
    ],
    "fields": [{"name": "title", "type": "string"}],
    "dataset": {"name": "items", "path": "items.sqlite", "identity": ["title"]},
    "schedule": {"every": "6h", "jitter": "5m"},
}


def test_schedule_parses_valid_durations_and_source_overrides() -> None:
    """Valid [schedule] and per-source every parse cleanly."""
    assert parse_duration("60s") == timedelta(seconds=60)
    assert parse_duration("1m") == timedelta(minutes=1)
    assert parse_duration("6h") == timedelta(hours=6)
    assert parse_duration("30d") == timedelta(days=30)
    assert parse_duration("0s", allow_zero=True) == timedelta(0)

    job = Job.model_validate(BASE_JOB)
    assert job.schedule == ScheduleSpec(every="6h", jitter="5m")
    assert source_every(job, job.sources[0]) == timedelta(hours=6)
    assert source_every(job, job.sources[1]) == timedelta(hours=1)
    dumped = job.redacted_dump()
    assert dumped["schedule"] == {"every": "6h", "jitter": "5m"}
    assert dumped["sources"][1]["every"] == "1h"

    plain = Job.model_validate(
        {
            k: v
            for k, v in {
                **BASE_JOB,
                "sources": [{"path": "a.html", "url": "https://a.example/items"}],
            }.items()
            if k != "schedule"
        }
    )
    assert plain.schedule is None
    assert "schedule" not in plain.redacted_dump()
    assert "every" not in plain.redacted_dump()["sources"][0]


@pytest.mark.parametrize(
    "bad_schedule",
    [
        {"every": "59s"},
        {"every": "0m"},
        {"every": "31d"},
        {"every": "721h"},
        {"every": "-5m"},
        {"every": "5w"},
        {"every": "secret_value_123"},
        {"every": ""},
        {"every": 60},
        {"every": True},
        {"every": "1h", "jitter": "16m"},
        {"every": "4m", "jitter": "61s"},
        {"every": "1h", "jitter": "-1s"},
        {"every": "1h", "jitter": "bad_jitter_secret"},
        {"every": "1h", "jitter": 10},
        {"every": "1h", "unknown": "1m"},
        {},
    ],
)
def test_schedule_validation_errors_are_configuration_error_without_echoing_values(
    bad_schedule: dict[str, Any],
) -> None:
    """Invalid schedule raises ConfigurationError and never echoes input."""
    with pytest.raises(ConfigurationError) as exc_info:
        Job.model_validate({**BASE_JOB, "schedule": bad_schedule})
    text = f"{exc_info.value} {exc_info.value!r}"
    for leak in ("secret_value_123", "bad_jitter_secret", "59s", "31d", "721h", "16m"):
        assert leak not in text


@pytest.mark.parametrize("bad_every", ["30s", "0s", "31d", "not_a_duration", 120])
def test_source_every_validation_errors_are_configuration_error_without_echoing(
    bad_every: Any,
) -> None:
    """Per-source every validates bounds and units without echoing values."""
    with pytest.raises(ConfigurationError) as exc_info:
        parse_job(
            f"""
format_version = 1
[recipe]
strategy = "css"
record_selector = ".item"
[recipe.fields]
title = "h2::text"
[[fields]]
name = "title"
type = "string"
[[sources]]
path = "a.html"
every = {bad_every!r}
""".replace("True", "true"),
            format="toml",
        )
    assert "not_a_duration" not in str(exc_info.value)


def test_due_computation_with_per_source_every() -> None:
    """Due sources computed from last_attempt and per-source every."""
    job = Job.model_validate(BASE_JOB)
    t0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

    # Never attempted -> both sources are due.
    assert due_sources(job, (), t0) == job.sources

    statuses = (
        SourceStatus(
            source_url="https://a.example/items",
            last_success=t0,
            last_attempt=t0,
            stale=False,
        ),
        SourceStatus(
            source_url="https://b.example/items",
            last_success=t0,
            last_attempt=t0,
            stale=False,
        ),
    )
    # After 30m: neither 6h nor 1h source is due.
    assert due_sources(job, statuses, t0 + timedelta(minutes=30)) == ()
    # After 1h: only source 2 (every="1h") is due.
    assert due_sources(job, statuses, t0 + timedelta(hours=1)) == (job.sources[1],)
    # After 6h: both sources are due.
    assert due_sources(job, statuses, t0 + timedelta(hours=6)) == job.sources


def test_jitter_is_bounded_and_deterministic() -> None:
    """Jitter is deterministic per (job_path, source_url) and bounded <= every/4."""
    cap = timedelta(minutes=5)
    j1 = deterministic_jitter(Path("/jobs/a.toml"), "https://a.example/items", cap)
    j1_repeat = deterministic_jitter(Path("/jobs/a.toml"), "https://a.example/items", cap)
    j2 = deterministic_jitter(Path("/jobs/a.toml"), "https://b.example/items", cap)
    j3 = deterministic_jitter(Path("/jobs/b.toml"), "https://a.example/items", cap)

    assert j1 == j1_repeat
    assert j1 != j2
    assert j1 != j3
    assert timedelta(0) <= j1 <= cap
    assert timedelta(0) <= j2 <= cap
    assert deterministic_jitter(Path("/jobs/a.toml"), "https://a.example/items", timedelta(0)) == (
        timedelta(0)
    )

    job = Job.model_validate(BASE_JOB)
    t0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    statuses = (
        SourceStatus(
            source_url="https://a.example/items", last_success=t0, last_attempt=t0, stale=False
        ),
        SourceStatus(
            source_url="https://b.example/items", last_success=t0, last_attempt=t0, stale=False
        ),
    )
    delay = next_sleep_seconds(job, statuses, t0, job_path=Path("/jobs/a.toml"))
    expected = timedelta(hours=1) + deterministic_jitter(
        Path("/jobs/a.toml"), "https://b.example/items", cap
    )
    assert delay == pytest.approx(expected.total_seconds())


def test_lock_held_and_stale_lock_replacement(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Live lock raises RunInProgressError; stale lock is replaced with WARNING."""
    db_path = tmp_path / "items.sqlite"
    t0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    lpath = lock_path(db_path)

    with dataset_lock(db_path, timeout_seconds=60.0, source_count=2, clock=lambda: t0):
        assert lpath.is_file()
        if os.name != "nt":
            assert stat.S_IMODE(lpath.stat().st_mode) == 0o600
        with (
            pytest.raises(RunInProgressError) as exc_info,
            dataset_lock(db_path, timeout_seconds=60.0, source_count=2, clock=lambda: t0),
        ):
            pass
        assert exc_info.value.location == "dataset.path"
        assert str(os.getpid()) not in str(exc_info.value)
        assert str(db_path) not in str(exc_info.value)
    assert not lpath.exists()

    # Stale lock by expired age (> operation_timeout * sources + 1h = 120s + 3600s).
    lpath.write_text(
        f'{{"pid": {os.getpid()}, "started_at": "{t0.isoformat()}"}}', encoding="utf-8"
    )
    later = t0 + timedelta(hours=2)
    with (
        caplog.at_level("WARNING", logger="rastro"),
        dataset_lock(db_path, timeout_seconds=60.0, source_count=2, clock=lambda: later),
    ):
        assert lpath.is_file()
    assert any("stale_lock" in record.getMessage() for record in caplog.records)
    assert not lpath.exists()

    # Stale lock by dead PID.
    caplog.clear()
    lpath.write_text(f'{{"pid": 99999999, "started_at": "{t0.isoformat()}"}}', encoding="utf-8")
    with (
        caplog.at_level("WARNING", logger="rastro"),
        dataset_lock(
            db_path,
            timeout_seconds=60.0,
            source_count=1,
            clock=lambda: t0,
            is_pid_alive=lambda _: False,
        ),
    ):
        assert lpath.is_file()
    assert any("stale_lock" in record.getMessage() for record in caplog.records)
    assert not lpath.exists()


def test_checkpoint_roundtrip_and_permissions(tmp_path: Path) -> None:
    """.run.json is written atomically with mode 0600 and cleared on completion."""
    db_path = tmp_path / "items.sqlite"
    cpath = write_checkpoint(db_path, "run-1", ((0, "https://a.example/items"),), dataset="items")
    assert cpath == checkpoint_path(db_path)
    assert cpath.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(cpath.stat().st_mode) == 0o600
    assert read_checkpoint(db_path, dataset="items") == (
        "run-1",
        ((0, "https://a.example/items"),),
    )
    clear_checkpoint(db_path)
    assert not cpath.exists()
    assert read_checkpoint(db_path, dataset="items") is None


def test_checkpoint_keys_same_url_sources_by_index(tmp_path: Path) -> None:
    """Two sources sharing one URL (other regions) complete independently."""
    db_path = tmp_path / "items.sqlite"
    url = "https://a.example/items"
    write_checkpoint(db_path, "run-1", ((0, url), (2, url), (0, url)), dataset="items")
    assert read_checkpoint(db_path, dataset="items") == ("run-1", ((0, url), (2, url)))
