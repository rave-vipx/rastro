"""``rastro new --explore JOB -o NEW``: explore a job's first page and write the result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rastro.application.navigation_file import explore_job_file
from rastro.domain.models import ConfigurationError


def add_explore_options(new: argparse.ArgumentParser) -> None:
    new.add_argument(
        "--explore",
        type=Path,
        metavar="JOB",
        help="Explore JOB's first page with a model and write its recipe with page steps",
    )
    new.add_argument(
        "--expect", type=int, metavar="N", help="With --explore: stop once N records show"
    )


def explore_command(args: argparse.Namespace, target: Path | None) -> int:
    if target is None:
        raise ConfigurationError("Pass -o FILE.toml to write the explored job")
    result = explore_job_file(
        args.explore,
        target,
        model=args.model or "env",
        expected_records=args.expect,
        allow_remote=args.allow_remote,
    )
    payload = {"schema_version": 1, "created": str(target)}
    payload |= result.model_dump(
        mode="json", include={"records_before", "records_after", "stop_reason", "model_calls"}
    )
    print(json.dumps(payload))
    return 0
