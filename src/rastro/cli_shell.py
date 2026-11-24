"""`rastro shell JOB`: an interactive Python shell on one source's parsed page.

The page is the job's local file, or its URL rendered once in a guarded browser with the
job's own policy (allowed hosts, SSRF, robots, budget). The shell gets ``job``, ``page`` (a
parsed page with ``css`` and ``xpath``), ``html`` and ``url``. IPython is used when it is
installed; otherwise the standard library's ``code.interact``.
"""

from __future__ import annotations

import argparse
import code
from pathlib import Path
from typing import Any

from rastro.application.job_loader import load_job, validate_job_paths
from rastro.application.live_page import load_live_page
from rastro.domain.job_results import JobExitCode
from rastro.domain.jobs import FileJobSource, Job
from rastro.domain.models import ConfigurationError, Limits
from rastro.scrape_target import read_bounded

__all__ = ["add_shell_command", "shell_command"]

BANNER = (
    "Rastro shell. Names: job, page (page.css('h2::text').getall()), html, url.\n"
    "Page text is untrusted data. Exit with Ctrl-D."
)


def add_shell_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``rastro shell``."""
    shell = subcommands.add_parser(
        "shell", help="Open an interactive Python shell on a job source's parsed page"
    )
    shell.add_argument("file", type=Path, help="Job file (.toml or .json)")
    shell.add_argument("--source", type=int, default=1, help="One-based source (default: 1)")
    shell.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files",
    )


def shell_command(args: argparse.Namespace) -> int:
    job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
    html, url = _page(job, args.file, args.source, args.allow_absolute_paths)
    from scrapling.parser import Selector

    page = Selector(html, url=url, adaptive=False, huge_tree=False)
    interact({"job": job, "page": page, "html": html, "url": url})
    return int(JobExitCode.SUCCESS)


def _page(job: Job, path: Path, index: int, allow_absolute_paths: bool) -> tuple[str, str]:
    if not 1 <= index <= len(job.sources):
        raise ConfigurationError("source is out of range", location="source")
    item = job.sources[index - 1]
    if isinstance(item, FileJobSource):
        files, _ = validate_job_paths(
            job, base_dir=path.parent, allow_absolute_paths=allow_absolute_paths
        )
        limits = job.limits or Limits()
        html = read_bounded(files[index - 1], limits.max_input_bytes(item.representation))
        return html, item.resolved_source_url()
    page = load_live_page(job, index)
    return page.html, page.url


def interact(namespace: dict[str, Any]) -> None:
    """IPython when installed, else the standard library console; returns on end of input."""
    try:
        from IPython import start_ipython  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        code.interact(banner=BANNER, local=namespace, exitmsg="")
        return
    start_ipython(argv=["--no-banner"], user_ns=namespace)
