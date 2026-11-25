"""`rastro job estimate`: dry-run crawl request and duration estimate without network fetches."""

from __future__ import annotations

import argparse
from pathlib import Path

from rastro.application.crawl_estimate import estimate
from rastro.application.job_loader import load_job
from rastro.domain.job_results import JobExitCode

__all__ = ["add_estimate_command", "estimate_command"]


def add_estimate_command(jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``rastro job estimate``."""
    est = jobs.add_parser(
        "estimate",
        help="Estimate maximum requests and crawl duration from job caps and pacing; never fetches",
    )
    est.add_argument("file", type=Path, help="Job file (.toml or .json)")
    est.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files and recipe memory",
    )


def estimate_command(args: argparse.Namespace) -> int:
    job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
    report = estimate(job)
    print(report.model_dump_json(indent=2))
    return int(JobExitCode.SUCCESS)
