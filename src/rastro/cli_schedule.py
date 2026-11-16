"""Scheduler CLI (`rastro job watch`, SCHED-002)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rastro.application.errors import RunInProgressError
from rastro.application.scheduler import watch_job
from rastro.cli_errors import classify
from rastro.cli_hook import add_hook_argument, hook_argv, load_summary, notify
from rastro.cli_trust import add_trust_arguments, trust_options
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobExitCode


def add_schedule_command(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    watch = jobs.add_parser("watch", help="Run due job sources on their [schedule]")
    watch.add_argument("file", type=Path)
    watch.add_argument(
        "--once", action="store_true", help="Run one scheduler tick and exit with its exit code"
    )
    watch.add_argument("--drift", action="store_true", help="Measure and report structural drift")
    watch.add_argument("--output", choices=("json",), default="json")
    watch.add_argument("--allow-absolute-paths", action="store_true")
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
