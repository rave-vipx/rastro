"""Operator-only failure hook for `rastro job load|run` (OPS, SECURITY.md).

``--on-failure CMD`` or ``RASTRO_ON_FAILURE`` names a command that runs when the job exits
non-zero after running (1, 3, 4 or 5). It is never read from a job file. The command is
split with ``shlex`` and run without a shell, with a timeout, and receives a small JSON
summary on stdin: outcome codes, hosts and drift levels only, never records, page content,
paths, queries or secrets. A hook failure is reported on stderr and never changes the exit
code. The command line itself is never echoed, since it may hold a webhook token.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from rastro.application.run_log import host
from rastro.domain.failure_reason import failure_detail
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ConfigurationError

ENV = "RASTRO_ON_FAILURE"
TIMEOUT_SECONDS = 30.0


def add_hook_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--on-failure",
        metavar="CMD",
        help=f"Run CMD (no shell) with a JSON summary on stdin when the job exits non-zero; "
        f"overrides {ENV}",
    )


def hook_argv(args: argparse.Namespace) -> list[str] | None:
    """Parse the hook before the job runs, so a bad command line is a usage error (exit 2)."""
    raw = args.on_failure if args.on_failure is not None else os.environ.get(ENV, "")
    try:
        argv = shlex.split(raw)
    except ValueError:
        raise ConfigurationError(
            f"--on-failure or {ENV} is not a valid command line", location="on_failure"
        ) from None
    return argv or None


def _summary(
    job: Path, command: str, status: str, code: int, sources: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": "rastro_job_failure",
        "job": job.name,
        "command": command,
        "status": status,
        "exit_code": code,
        "sources": sources,
    }


def run_summary(job: Path, report: JobRunResult[Any]) -> dict[str, Any]:
    sources = [
        {
            "source_index": index,
            "host": host(result.source_url),
            "outcome": result.status.value,
            "reason": failure_detail(result),
            "drift_risk": None,
        }
        for index, result in enumerate(report.results, start=1)
    ]
    return _summary(job, "run", report.job.status, int(report.exit_code), sources)


def load_summary(job: Path, loaded: JobLoadResult) -> dict[str, Any]:
    drift = loaded.drift or ()
    open_urls = {item.source_url for item in loaded.open_drift}
    sources = [
        {
            "source_index": index,
            "host": host(source.source_url),
            "outcome": source.status.value,
            "reason": source.reason,
            "issues": sorted({issue.code for issue in source.issues}),
            "drift_risk": drift[index - 1].risk if index <= len(drift) else None,
            "open_drift": source.source_url in open_urls,
        }
        for index, source in enumerate(loaded.sources, start=1)
    ]
    return _summary(job, "load", loaded.status, int(loaded.exit_code), sources)


def notify(argv: list[str] | None, summary: dict[str, Any]) -> None:
    """Run the hook for a non-zero exit; report its failure on stderr only."""
    if argv is None or summary["exit_code"] == 0:
        return
    payload = json.dumps(summary, ensure_ascii=True).encode()
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _issue(f"On-failure hook timed out after {TIMEOUT_SECONDS:g} seconds")
    except OSError as error:
        _issue(f"On-failure hook could not start: {error.strerror or 'operating system error'}")
    else:
        if completed.returncode != 0:
            _issue(f"On-failure hook exited with status {completed.returncode}")


def _issue(message: str) -> None:
    print(json.dumps({"issue": "on_failure_hook_failed", "message": message}), file=sys.stderr)
