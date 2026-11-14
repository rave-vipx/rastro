"""Operator commands: acknowledge open drift (DRIFT-005, ADR 0022)."""

import argparse
import json
from pathlib import Path

from rastro.application.job_maintenance import acknowledge_job_drift
from rastro.domain.job_results import JobExitCode

JOB_COMMANDS = frozenset({"ack-drift"})


def add_job_commands(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    ack = jobs.add_parser(
        "ack-drift", help="Acknowledge open drift so the drifted structure becomes the baseline"
    )
    ack.add_argument("file", type=Path)
    which = ack.add_mutually_exclusive_group(required=True)
    which.add_argument("--source", type=int, help="One-based source index")
    which.add_argument("--all", action="store_true", help="Every source of the job")
    ack.add_argument("--allow-absolute-paths", action="store_true")


def job_command(args: argparse.Namespace) -> int:
    acknowledged = acknowledge_job_drift(
        args.file, args.source, allow_absolute_paths=args.allow_absolute_paths
    )
    print(json.dumps({"schema_version": 1, "acknowledged": list(acknowledged)}, indent=2))
    return int(JobExitCode.SUCCESS)
