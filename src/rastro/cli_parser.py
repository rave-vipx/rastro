"""The ``rastro`` argument parser: every command and flag, without the handlers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NoReturn

from rastro import __version__
from rastro.cli_agent import add_commands as add_agent_commands
from rastro.cli_config import add_commands as add_config_commands
from rastro.cli_dataset import add_commands
from rastro.cli_errors import UsageError
from rastro.cli_estimate import add_estimate_command
from rastro.cli_hook import add_hook_argument
from rastro.cli_hub_group import add_command as add_hub_group
from rastro.cli_model_setup import add_command as add_model_command
from rastro.cli_notify import add_notify_parser
from rastro.cli_ops import add_health_command
from rastro.cli_ops import add_job_commands as add_ops_job_commands
from rastro.cli_recipe import add_commands as add_recipe_commands
from rastro.cli_schedule import add_schedule_command
from rastro.cli_shell import add_shell_command
from rastro.cli_stats import add_stats_command
from rastro.cli_studio import add_command as add_studio_command
from rastro.cli_suggest import add_commands as add_suggest_commands
from rastro.cli_trust import add_trust_arguments

__all__ = ["ArgumentParser", "build_parser"]


class ArgumentParser(argparse.ArgumentParser):
    """Raise usage errors so `rastro job` can report them as JSON instead of human text."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        prog="rastro", description="Rastro: verifiable, schema-first extraction"
    )
    parser.add_argument("--version", action="version", version=f"rastro {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Write structured logs to stderr")
    add_trust_arguments(parser, root=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="Run a synthetic JSON-LD extraction without network access")
    subcommands.add_parser("doctor", help="Report installed core versions and capabilities as JSON")
    validate = subcommands.add_parser("validate-recipe", help="Validate a local declarative recipe")
    validate.add_argument("path", type=Path, help="Recipe JSON file (at most 64 KiB)")

    mcp = subcommands.add_parser("mcp", help="Serve jobs and datasets to an agent over stdio MCP")
    mcp.add_argument("--root", type=Path, default=Path(), help="Workspace root (default: cwd)")
    mcp.add_argument(
        "--allow-override-gates",
        action="store_true",
        help="Let the agent call recipe_promote with override_gates (off: page content can "
        "prompt-inject the agent; run rastro recipe promote --override-gates yourself)",
    )
    add_trust_arguments(mcp)
    add_shell_command(subcommands)

    job_parser = subcommands.add_parser("job", help="Validate, run, or inspect declarative jobs")
    job_commands = job_parser.add_subparsers(dest="job_command", required=True)

    job_validate = job_commands.add_parser("validate", help="Validate a TOML or JSON job file")
    job_validate.add_argument("file", type=Path, help="Job file (.toml or .json)")
    job_validate.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files and recipe memory",
    )

    job_run = job_commands.add_parser("run", help="Execute a TOML or JSON job file")
    job_run.add_argument("file", type=Path, help="Job file (.toml or .json)")
    job_run.add_argument(
        "--output",
        choices=("json",),
        default="json",
        help="Machine-readable output format (default: json)",
    )
    job_run.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files and recipe memory",
    )
    add_trust_arguments(job_run)
    add_hook_argument(job_run)

    job_preview = job_commands.add_parser(
        "preview", help="Preview what a job would load into its [dataset]; writes nothing"
    )
    job_preview.add_argument("file", type=Path, help="Job file (.toml or .json)")
    job_preview.add_argument(
        "--output",
        choices=("table", "json"),
        default="table",
        help="Human table (default) or machine-readable JSON",
    )
    job_preview.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files, recipe memory and the dataset",
    )
    add_trust_arguments(job_preview)

    job_schema = job_commands.add_parser("schema", help="Print JSON Schema for declarative jobs")
    job_schema.add_argument(
        "--target",
        choices=("job", "run", "preview", "load", "health"),
        default="job",
        help="Schema to print: 'job' input (default), 'run', 'preview', 'load' or 'health' output",
    )
    add_commands(subcommands, job_commands)
    add_ops_job_commands(job_commands)
    add_estimate_command(job_commands)
    add_stats_command(job_commands)
    add_schedule_command(job_commands)
    add_health_command(subcommands)
    add_recipe_commands(subcommands)
    add_studio_command(subcommands)
    add_hub_group(subcommands)
    add_notify_parser(subcommands)
    add_model_command(subcommands)
    add_suggest_commands(subcommands)
    add_agent_commands(subcommands)
    add_config_commands(subcommands, job_commands)
    return parser
