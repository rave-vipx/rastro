"""Job schedule, per-source intervals and deterministic jitter."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, ModelWrapValidatorHandler, StrictStr, ValidationError, model_validator

from rastro.domain.dataset import SourceStatus
from rastro.domain.job_contract import RedactedContract
from rastro.domain.models import ConfigurationError

if TYPE_CHECKING:
    from rastro.domain.job_sources import JobSource
    from rastro.domain.jobs import Job

_DURATION_RE = re.compile(r"^([0-9]+)([smhd])$")
_TIME_RE = re.compile(r"^[0-9]{2}:[0-9]{2}$")
_UNITS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
MIN_EVERY, MAX_EVERY = timedelta(minutes=1), timedelta(days=30)


def parse_duration(
    raw: object, *, location: str = "schedule.every", allow_zero: bool = False
) -> timedelta:
    """Parse a bounded duration string (`s`/`m`/`h`/`d`) without echoing invalid input."""
    if not isinstance(raw, str) or (m := _DURATION_RE.fullmatch(raw)) is None:
        raise ConfigurationError("Duration must be <int><s|m|h|d>", location=location)
    td = timedelta(seconds=int(m.group(1)) * _UNITS[m.group(2)])
    if td < (timedelta(0) if allow_zero else MIN_EVERY) or td > MAX_EVERY:
        raise ConfigurationError("Duration is out of bounds", location=location)
    return td


def _check_time_of_day(at: object, every: timedelta) -> None:
    if (
        not isinstance(at, str)
        or _TIME_RE.fullmatch(at) is None
        or int(at[:2]) > 23
        or int(at[3:]) > 59
    ):
        raise ConfigurationError("at must be HH:MM (24 hours)", location="schedule.at")
    if every % timedelta(days=1):
        raise ConfigurationError("at needs every in whole days", location="schedule.at")


class ScheduleSpec(RedactedContract):
    """Optional `[schedule]` block: `every`, an optional `at` time of day, bounded `jitter`."""

    every: StrictStr
    at: StrictStr | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description='Time of day as "HH:MM" in the job timezone; needs every in whole days.',
    )
    jitter: StrictStr | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="wrap")
    @classmethod
    def validate_schedule(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        if (
            not isinstance(data, dict)
            or set(data) - {"every", "jitter", "at"}
            or not data.get("every")
        ):
            raise ConfigurationError("Invalid [schedule] configuration", location="schedule")
        every_td = parse_duration(data["every"], location="schedule.every")
        if data.get("at") is not None:
            _check_time_of_day(data["at"], every_td)
        if data.get("jitter") is not None:
            jitter_td = parse_duration(data["jitter"], location="schedule.jitter", allow_zero=True)
            if jitter_td > every_td / 4:
                raise ConfigurationError("jitter exceeds every / 4", location="schedule.jitter")
        try:
            return handler(data)
        except ValidationError:
            raise ConfigurationError(
                "Invalid [schedule] configuration", location="schedule"
            ) from None

    @property
    def every_delta(self) -> timedelta:
        return parse_duration(self.every, location="schedule.every")

    @property
    def jitter_delta(self) -> timedelta:
        return (
            timedelta(0)
            if self.jitter is None
            else parse_duration(self.jitter, location="schedule.jitter", allow_zero=True)
        )


Schedule = ScheduleSpec


def source_evidence_url(job: Job, source: JobSource) -> str:
    raw = source.resolved_source_url() if hasattr(source, "resolved_source_url") else source.url
    return (source.region or job.region).to_source(raw).evidence_url


def source_every(job: Job, source: JobSource) -> timedelta:
    if source.every is not None:
        return parse_duration(source.every, location="sources.every")
    if job.schedule is None:
        raise ConfigurationError("Sources without every require [schedule]", location="schedule")
    return job.schedule.every_delta


def source_next_due(job: Job, source: JobSource, last_attempt: datetime) -> datetime:
    """Next due time; a job-level time of day applies when the source has no own interval."""
    if source.every is None and job.schedule is not None and job.schedule.at is not None:
        from rastro.domain.schedule_words import next_due_at

        return next_due_at(job.schedule, last_attempt, job.region.timezone)
    return last_attempt + source_every(job, source)


def deterministic_jitter(job_path: str | Path, source_url: str, max_jitter: timedelta) -> timedelta:
    """Deterministic offset in `[0, max_jitter]` seeded from job path and source URL."""
    if max_jitter <= timedelta(0):
        return timedelta(0)
    digest = hashlib.sha256(f"{Path(job_path).as_posix()}\0{source_url}".encode()).digest()
    ratio = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
    return timedelta(seconds=ratio * max_jitter.total_seconds())


def _by_url(
    statuses: Sequence[SourceStatus] | Mapping[str, SourceStatus],
) -> dict[str, SourceStatus]:
    return dict(statuses) if isinstance(statuses, Mapping) else {s.source_url: s for s in statuses}


def due_sources(
    job: Job, statuses: Sequence[SourceStatus] | Mapping[str, SourceStatus], now: datetime
) -> tuple[JobSource, ...]:
    """Sources whose `last_attempt` is absent or at least `every` before `now`."""
    by_url = _by_url(statuses)
    return tuple(
        s
        for s in job.sources
        if (st := by_url.get(source_evidence_url(job, s))) is None
        or now >= source_next_due(job, s, st.last_attempt)
    )


def next_sleep_seconds(
    job: Job,
    statuses: Sequence[SourceStatus] | Mapping[str, SourceStatus],
    now: datetime,
    *,
    job_path: str | Path = "",
) -> float:
    """Seconds from `now` until the earliest source's next due time plus deterministic jitter."""
    by_url = _by_url(statuses)
    base = job.schedule.jitter_delta if job.schedule is not None else timedelta(0)
    wakes = [
        (
            now
            if (st := by_url.get(u := source_evidence_url(job, s))) is None
            else source_next_due(job, s, st.last_attempt)
        )
        + deterministic_jitter(job_path, u, min(base, source_every(job, s) / 4))
        for s in job.sources
    ]
    return max(0.0, (min(wakes) - now).total_seconds()) if wakes else 0.0
