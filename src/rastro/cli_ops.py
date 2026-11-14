"""Operator commands: acknowledge open drift, forget a removed source (DRIFT-005, DATA-004)."""

import argparse
import json
from pathlib import Path

from rastro.application.job_maintenance import acknowledge_job_drift, forget_job_source
from rastro.domain.job_results import JobExitCode

JOB_COMMANDS = frozenset({"ack-drift", "forget-source"})


def add_job_commands(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    ack = jobs.add_parser(
        "ack-drift", help="Acknowledge open drift so the drifted structure becomes the baseline"
    )
    ack.add_argument("file", type=Path)
    which = ack.add_mutually_exclusive_group(required=True)
    which.add_argument("--source", type=int, help="One-based source index")
    which.add_argument("--all", action="store_true", help="Every source of the job")
    ack.add_argument("--allow-absolute-paths", action="store_true")
    forget = jobs.add_parser(
        "forget-source", help="Forget the status and drift state of a source no longer in the job"
    )
    forget.add_argument("file", type=Path)
    forget.add_argument("--url", required=True, help="The source URL as `dataset status` shows")
    forget.add_argument("--allow-absolute-paths", action="store_true")


def job_command(args: argparse.Namespace) -> int:
    if args.job_command == "forget-source":
        payload = forget_job_source(
            args.file, args.url, allow_absolute_paths=args.allow_absolute_paths
        )
    else:
        acknowledged = acknowledge_job_drift(
            args.file, args.source, allow_absolute_paths=args.allow_absolute_paths
        )
        payload = {"schema_version": 1, "acknowledged": list(acknowledged)}
    print(json.dumps(payload, indent=2))
    return int(JobExitCode.SUCCESS)
