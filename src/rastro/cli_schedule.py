"""Scheduler CLI (`rastro job watch`, SCHED-002)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rastro.application.errors import RunInProgressError
from rastro.application.scheduler import watch_job
from rastro.cli_errors import classify
from rastro.cli_hook import add_hook_argument, hook_argv, load_summary, notify
from rastro.cli_trust import add_trust_arguments, trust_options
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobExitCode
from rastro.domain.schedule_words import describe_schedule, next_runs, parse_schedule_words


def add_schedule_command(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    words = jobs.add_parser("schedule", help="Read a schedule in words and show its next runs")
    words.add_argument("words", help='For example "every day at 8:00" or "cada 6 horas"')
    words.add_argument("--timezone", default="UTC", help="IANA timezone for the time of day")
    watch = jobs.add_parser("watch", help="Run due job sources on their [schedule]")
    watch.add_argument("file", type=Path, help="Job file (.toml or .json)")
    watch.add_argument(
        "--once", action="store_true", help="Run one scheduler tick and exit with its exit code"
    )
    watch.add_argument("--drift", action="store_true", help="Measure and report structural drift")
    watch.add_argument(
        "--output", choices=("json",), default="json", help="Machine-readable output format"
    )
    watch.add_argument(
        "--allow-absolute-paths", action="store_true", help="Allow absolute paths in the job file"
    )
    add_trust_arguments(watch)
    add_hook_argument(watch)


def watch_job_command(args: argparse.Namespace) -> int:
    hook = hook_argv(args)

    def _on_tick(loaded: JobLoadResult) -> None:
        print(loaded.model_dump_json(indent=2), flush=True)
        notify(hook, load_summary(args.file, loaded))

    try:
        return watch_job(
            args.file,
            once=args.once,
            drift=args.drift,
            allow_absolute_paths=args.allow_absolute_paths,
            on_tick=_on_tick,
            **trust_options(args),
        )
    except RunInProgressError as error:
        print(json.dumps(classify(error)), file=sys.stderr)
        return int(JobExitCode.NEEDS_ATTENTION)


def schedule_words_command(args: argparse.Namespace) -> int:
    spec = parse_schedule_words(args.words)
    runs = next_runs(spec, datetime.now(UTC), timezone=args.timezone)
    payload = {
        "schedule": spec.model_dump(mode="json"),
        "in_words": {code: describe_schedule(spec, code) for code in ("en", "es")},
        "next_runs": [moment.isoformat() for moment in runs],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return int(JobExitCode.SUCCESS)
