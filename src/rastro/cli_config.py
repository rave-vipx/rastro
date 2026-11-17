"""`rastro config` and `rastro job template`: every configurable point, generated from code
(N18, AGENT-001). Output goes to stdout; nothing is written."""

from __future__ import annotations

import argparse
import sys

from rastro import JobExitCode, __version__

COMMANDS = frozenset({"config"})


def add_commands(
    subcommands: argparse._SubParsersAction,  # type: ignore[type-arg]
    job_commands: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    config = subcommands.add_parser(
        "config", help="Print every job key, keyword argument, grant, variable and CLI flag"
    )
    config.add_argument(
        "--output",
        choices=("json", "markdown"),
        default="json",
        help="Machine-readable JSON (default) or the docs/configuration.md page",
    )
    template = job_commands.add_parser(
        "template", help="Print an annotated job file with every option commented"
    )
    template.add_argument(
        "--all",
        action="store_true",
        dest="all_variants",
        help="Also show every recipe, field, source, model and rule variant",
    )


def config_command(args: argparse.Namespace) -> int:
    from rastro.config_render import render_json, render_markdown

    sys.stdout.write(render_json(__version__) if args.output == "json" else render_markdown())
    return int(JobExitCode.SUCCESS)


def template_command(args: argparse.Namespace) -> int:
    from rastro.config_template import render_template

    sys.stdout.write(render_template(__version__, all_variants=args.all_variants))
    return int(JobExitCode.SUCCESS)
