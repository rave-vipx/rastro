"""Operator-only failure hook for `rastro job load|run` (OPS, SECURITY.md).

``--on-failure CMD`` or ``RASTRO_ON_FAILURE`` names a command that runs when the job exits
non-zero after running (1, 3, 4 or 5). It is never read from a job file. The command is
split with ``shlex`` and run without a shell, with a timeout, and receives a small JSON
summary on stdin: outcome codes, hosts and drift levels only, never records, page content,
file paths, queries or secrets. Payload version 2 keeps every version 1 key and adds
``report``, the run report (hosts, URL paths, counts and reasons; see
``docs/run-report.md``). A hook failure is reported on stderr and never changes the exit
code. The command line itself is never echoed, since it may hold a webhook token.

``--notify CHANNEL`` or ``RASTRO_NOTIFY`` adds built-in notifiers (email, Slack, Telegram,
webhook; see ``rastro.cli_notify``) that send a plain-language message built from the run
report on the same non-zero exits. A notifier failure is reported on stderr as
``notification_failed`` with the channel and a reason code, and never changes the exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rastro.application.notifications import Message, Notifier, deliver, message_for
from rastro.application.run_log import host
from rastro.application.run_report import report_for_run
from rastro.cli_notify import add_notify_argument, notifiers_from_env
from rastro.domain.failure_reason import failure_detail
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ConfigurationError
from rastro.domain.run_report import RunReport

ENV = "RASTRO_ON_FAILURE"
TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Hook:
    """What runs when a job needs attention: an optional command and built-in notifiers."""

    argv: list[str] | None
    notifiers: tuple[Notifier, ...] = ()


def add_hook_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--on-failure",
        metavar="CMD",
        help=f"Run CMD (no shell) with a JSON summary on stdin when the job exits non-zero; "
        f"overrides {ENV}",
    )
    add_notify_argument(parser)


def hook_argv(args: argparse.Namespace) -> Hook:
    """Parse the hook and notifiers before the job runs, so bad settings exit 2."""
    raw = args.on_failure if args.on_failure is not None else os.environ.get(ENV, "")
    try:
        argv = shlex.split(raw)
    except ValueError:
        raise ConfigurationError(
            f"--on-failure or {ENV} is not a valid command line", location="on_failure"
        ) from None
    return Hook(argv or None, notifiers_from_env(getattr(args, "notify", None)))


def _summary(
    job: Path,
    command: str,
    status: str,
    code: int,
    sources: list[dict[str, Any]],
    report: RunReport | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": 2,
        "event": "rastro_job_failure",
        "job": job.name,
        "command": command,
        "status": status,
        "exit_code": code,
        "sources": sources,
    }
    if report is not None:
        summary["report"] = report.model_dump(mode="json")
    return summary


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
    run_report = report.report or report_for_run(job.name, report)
    return _summary(job, "run", report.job.status, int(report.exit_code), sources, run_report)


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
    return _summary(job, "load", loaded.status, int(loaded.exit_code), sources, loaded.report)


def notify(hook: Hook, summary: dict[str, Any]) -> None:
    """Run the hook and notifiers for a non-zero exit; report failures on stderr only."""
    if summary["exit_code"] == 0:
        return
    if hook.notifiers:
        for channel, reason in deliver(hook.notifiers, _message(summary)):
            print(
                json.dumps({"issue": "notification_failed", "channel": channel, "reason": reason}),
                file=sys.stderr,
            )
    if hook.argv is not None:
        _run_command(hook.argv, summary)


def _message(summary: dict[str, Any]) -> Message:
    if summary.get("report") is not None:
        return message_for(RunReport.model_validate(summary["report"]))
    return Message(
        event="rastro_job_attention",
        subject=f"Rastro: {summary['job']} needs attention (exit {summary['exit_code']})",
        text=f"The job ended with status {summary['status']}.",
    )


def _run_command(argv: list[str], summary: dict[str, Any]) -> None:
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
