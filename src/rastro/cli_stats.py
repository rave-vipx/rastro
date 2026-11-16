"""`rastro job stats` and the `--max-model-cost` option (OBS-003..006, COST-002)."""

import argparse
import json
from pathlib import Path

from rastro.application.job_stats import GROUPINGS, job_stats
from rastro.domain.job_results import JobExitCode


def add_stats_command(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``job stats`` and give the model-calling commands ``--max-model-cost``."""
    for name in ("run", "load"):
        add_cost_argument(jobs.choices[name])
    stats = jobs.add_parser(
        "stats", help="Per-source throughput, latency, error rate, freshness and coverage"
    )
    stats.add_argument("file", type=Path)
    stats.add_argument("--since", help="Only runs finished within this window, e.g. 7d or 12h")
    stats.add_argument(
        "--by", choices=GROUPINGS, default="source", help="Grouping (default source)"
    )
    stats.add_argument("--allow-absolute-paths", action="store_true")


def add_cost_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-model-cost",
        type=float,
        metavar="USD",
        help="Stop calling the model once this cost would be exceeded (needs the job's "
        "[model] cost_per_call; overrides the job's max_cost)",
    )


def stats_command(args: argparse.Namespace) -> int:
    report = job_stats(
        args.file,
        since=args.since,
        by=args.by,
        allow_absolute_paths=args.allow_absolute_paths,
    )
    print(json.dumps(report, indent=2))
    return int(JobExitCode.SUCCESS)
