"""Operator commands: health, acknowledge open drift, forget a removed source (DRIFT-005,
DATA-004, REPAIR-005)."""

import argparse
import json
from pathlib import Path

from rastro.application.health import job_health
from rastro.application.job_maintenance import acknowledge_job_drift, forget_job_source
from rastro.application.job_preview import _cell
from rastro.domain.health import JobHealth
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


def add_health_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    health = subcommands.add_parser(
        "health", help="Stale sources, open drift and pending repairs of a job; exit 4 if any"
    )
    health.add_argument("file", type=Path)
    health.add_argument("--output", choices=("table", "json"), default="table")
    health.add_argument("--allow-absolute-paths", action="store_true")


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


def health_command(args: argparse.Namespace) -> int:
    report = job_health(args.file, allow_absolute_paths=args.allow_absolute_paths)
    print(report.model_dump_json(indent=2) if args.output == "json" else render_health(report))
    return int(report.exit_code)


def render_health(report: JobHealth) -> str:
    """One line per source; every cell is escaped, since URLs and reasons come from files."""
    lines = [
        f"Dataset {_cell(report.dataset)}: {'healthy' if report.healthy else 'needs attention'}",
        "#  status  last success  open drift  candidates  attention  source",
    ]
    for source in report.sources:
        status = "-" if source.stale is None else "stale" if source.stale else "fresh"
        drift = source.open_drift.risk if source.open_drift else "-"
        candidates = ",".join(f"v{v}" for v in source.quarantined_versions) or "-"
        success = source.last_success.isoformat() if source.last_success else "never"
        attention = ",".join(source.attention) or "-"
        lines.append(
            f"{source.source_index}  {status}  {success}  {drift}  {candidates}  {attention}  "
            f"{_cell(source.source_url)}"
        )
    for url in report.orphaned_sources:
        lines.append(f"not in job (rastro job forget-source): {_cell(url)}")
    return "\n".join(lines)
