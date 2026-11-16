"""Foreground persistent scheduler (`rastro job watch`, SCHED-001..007, SYNC-001)."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import structlog

from rastro.application.egress_trust import check_job_egress
from rastro.application.errors import RunInProgressError
from rastro.application.job_dataset import dataset_location, load_job_file, open_dataset
from rastro.application.job_loader import load_job
from rastro.application.run_lock import (
    clear_checkpoint,
    dataset_lock,
    read_checkpoint,
    source_key,
)
from rastro.domain.dataset import SourceStatus
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobExitCode
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError, Outcome
from rastro.domain.schedule import due_sources, next_sleep_seconds, source_evidence_url

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_BUSY_RETRY_SECONDS = 30.0  # wait before retrying a tick whose dataset another run holds


@contextmanager
def _signal_guard(on_stop: Callable[[], None]) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    prev_int, prev_term = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, lambda _s, _f: on_stop())
    signal.signal(signal.SIGTERM, lambda _s, _f: on_stop())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _merged_statuses(
    raw_path: Path,
    job: Job,
    attempted: dict[str, SourceStatus],
    *,
    allow_remote: bool = False,
    allow_env: tuple[str, ...] = (),
) -> dict[str, SourceStatus]:
    assert job.dataset is not None
    by_url = dict(attempted)
    if job.dataset.backend == "sqlite" and not raw_path.is_file():
        return by_url
    with open_dataset(job, raw_path, allow_remote=allow_remote, allow_env=allow_env) as store:
        for status in store.source_status(job.dataset.name):
            prior = by_url.get(status.source_url)
            if prior is None or status.last_attempt >= prior.last_attempt:
                by_url[status.source_url] = status
    return by_url


def _run_tick(
    file: Path,
    raw_path: Path,
    job: Job,
    now: datetime,
    attempted: dict[str, SourceStatus],
    *,
    drift: bool,
    allow_absolute_paths: bool,
    allow_remote: bool,
    allow_egress: bool,
    allow_stealth: bool,
    allow_env: tuple[str, ...],
    allow_private_network: bool,
    allow_ports: tuple[int, ...],
    ignore_robots: tuple[str, ...],
    max_model_cost: float | None,
    model_cost_per_call: float | None,
    should_stop: Callable[[], bool],
    on_tick: Callable[[JobLoadResult], None] | None,
    load_runner: Callable[[Path, Job], JobLoadResult | int] | None,
) -> int:
    assert job.dataset is not None
    statuses = _merged_statuses(
        raw_path, job, attempted, allow_remote=allow_remote, allow_env=allow_env
    )
    ckpt = read_checkpoint(raw_path, dataset=job.dataset.name)
    due = (
        tuple(s for i, s in enumerate(job.sources) if source_key(job, i, s) not in set(ckpt[1]))
        if ckpt is not None
        else ()
    ) or due_sources(job, statuses, now)
    if not due:
        return int(JobExitCode.SUCCESS)
    if load_runner is not None:
        out = load_runner(file, job.model_copy(update={"sources": due}))
        clear_checkpoint(raw_path)
        for src in due:
            url = source_evidence_url(job, src)
            attempted[url] = SourceStatus(
                source_url=url, last_success=now, last_attempt=now, stale=False
            )
        if isinstance(out, JobLoadResult) and on_tick is not None:
            on_tick(out)
        return int(out.exit_code) if isinstance(out, JobLoadResult) else int(out)
    loaded = load_job_file(
        file,
        drift=drift,
        allow_absolute_paths=allow_absolute_paths,
        allow_remote=allow_remote,
        allow_egress=allow_egress,
        allow_stealth=allow_stealth,
        allow_env=allow_env,
        allow_private_network=allow_private_network,
        allow_ports=allow_ports,
        ignore_robots=ignore_robots,
        max_model_cost=max_model_cost,
        model_cost_per_call=model_cost_per_call,
        sources=due,
        should_stop=should_stop,
        _lock_held=True,
    )
    for item in loaded.sources:
        ok = item.status == Outcome.SUCCESS and not item.inconsistent
        attempted[item.source_url] = SourceStatus(
            source_url=item.source_url,
            last_success=now if ok else None,
            last_attempt=now,
            stale=not ok,
        )
    if on_tick is not None:
        on_tick(loaded)
    return int(loaded.exit_code)


def watch_job(
    path: str | Path,
    *,
    once: bool = False,
    drift: bool = False,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    allow_private_network: bool = False,
    allow_ports: Iterable[int] = (),
    ignore_robots: Iterable[str] = (),
    max_model_cost: float | None = None,
    model_cost_per_call: float | None = None,
    on_tick: Callable[[JobLoadResult], None] | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], object] | None = None,
    load_runner: Callable[[Path, Job], JobLoadResult | int] | None = None,
    max_ticks: int | None = None,
) -> int:
    """Run due sources on a schedule using the job's `[schedule]` and `[dataset]` blocks."""
    file = Path(path)
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    allow_env_tuple = tuple(allow_env)
    ports, robots = tuple(allow_ports), tuple(ignore_robots)
    check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env_tuple)
    if job.schedule is None or job.dataset is None:
        missing = "schedule" if job.schedule is None else "dataset"
        raise ConfigurationError(f"job watch requires a [{missing}] block", location=missing)
    raw_path = dataset_location(file, job, allow_absolute_paths=allow_absolute_paths)
    stop = threading.Event()
    clock_fn, sleep_fn = clock or (lambda: datetime.now(UTC)), sleep or stop.wait
    timeout = job.acquisition.operation_timeout_seconds if job.acquisition is not None else 60.0
    attempted: dict[str, SourceStatus] = {}
    ticks = 0
    with _signal_guard(stop.set):
        while not stop.is_set():
            now, skipped = clock_fn(), False
            try:
                with dataset_lock(
                    raw_path, timeout_seconds=timeout, source_count=len(job.sources), clock=clock_fn
                ):
                    exit_code = _run_tick(
                        file,
                        raw_path,
                        job,
                        now,
                        attempted,
                        drift=drift,
                        allow_absolute_paths=allow_absolute_paths,
                        allow_remote=allow_remote,
                        allow_egress=allow_egress,
                        allow_stealth=allow_stealth,
                        allow_env=allow_env_tuple,
                        allow_private_network=allow_private_network,
                        allow_ports=ports,
                        ignore_robots=robots,
                        max_model_cost=max_model_cost,
                        model_cost_per_call=model_cost_per_call,
                        should_stop=stop.is_set,
                        on_tick=on_tick,
                        load_runner=load_runner,
                    )
            except RunInProgressError:
                if once:
                    raise
                _LOG.warning("watch_tick_skipped", reason="run_in_progress")
                exit_code, skipped = int(JobExitCode.NEEDS_ATTENTION), True
            ticks += 1
            if stop.is_set():
                return int(JobExitCode.SUCCESS)
            if once or (max_ticks is not None and ticks >= max_ticks):
                return exit_code
            statuses = _merged_statuses(
                raw_path, job, attempted, allow_remote=allow_remote, allow_env=allow_env_tuple
            )
            wait = next_sleep_seconds(job, statuses, clock_fn(), job_path=file)
            sleep_fn(max(wait, _BUSY_RETRY_SECONDS) if skipped else wait)
    return int(JobExitCode.SUCCESS)
