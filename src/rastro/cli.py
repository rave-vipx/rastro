"""Machine-readable CLI. Stdout is JSON; exit codes distinguish outcomes and config errors."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn, TextIO

from pydantic import ValidationError
from pydantic_core import SchemaError

from rastro import (
    JobExitCode,
    RastroError,
    Recipe,
    __version__,
    job_json_schema,
    job_run_json_schema,
    load_job,
    run_job_file,
)
from rastro.application.job_preview import preview_job_file, render_preview_table
from rastro.application.job_summary import job_validation_summary
from rastro.application.operator_trust import set_process_trust
from rastro.cli_dataset import add_commands, dataset_command, load_job_command
from rastro.cli_errors import UsageError, classify, missing_extra_error
from rastro.cli_recipe import add_commands as add_recipe_commands
from rastro.cli_recipe import recipe_command
from rastro.cli_studio import add_command as add_studio_command
from rastro.cli_studio import studio_command
from rastro.cli_suggest import add_commands as add_suggest_commands
from rastro.cli_suggest import new_command, suggest_command
from rastro.cli_trust import add_trust_arguments, apply_trust, hoist_trust_flags
from rastro.domain.job_dataset import job_preview_json_schema
from rastro.domain.job_load import job_load_json_schema

# Commands whose errors are reported as one JSON object on stderr.
_JSON_ERRORS = frozenset({"job", "recipe", "suggest", "new"})


class _ArgumentParser(argparse.ArgumentParser):
    """Raise usage errors so `rastro job` can report them as JSON instead of human text."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="rastro", description="Rastro: verifiable, schema-first extraction"
    )
    parser.add_argument("--version", action="version", version=f"rastro {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Write structured logs to stderr")
    add_trust_arguments(parser)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="Run a synthetic JSON-LD extraction without network access")
    subcommands.add_parser("doctor", help="Report installed core versions and capabilities as JSON")
    validate = subcommands.add_parser("validate-recipe", help="Validate a local declarative recipe")
    validate.add_argument("path", type=Path)

    mcp = subcommands.add_parser("mcp", help="Serve jobs and datasets to an agent over stdio MCP")
    mcp.add_argument("--root", type=Path, default=Path(), help="Workspace root (default: cwd)")

    job_parser = subcommands.add_parser("job", help="Validate, run, or inspect declarative jobs")
    job_commands = job_parser.add_subparsers(dest="job_command", required=True)

    job_validate = job_commands.add_parser("validate", help="Validate a TOML or JSON job file")
    job_validate.add_argument("file", type=Path)
    job_validate.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="Allow absolute paths for local source files and recipe memory",
    )

    job_run = job_commands.add_parser("run", help="Execute a TOML or JSON job file")
    job_run.add_argument("file", type=Path)
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

    job_preview = job_commands.add_parser(
        "preview", help="Preview what a job would load into its [dataset]; writes nothing"
    )
    job_preview.add_argument("file", type=Path)
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

    job_schema = job_commands.add_parser("schema", help="Print JSON Schema for declarative jobs")
    job_schema.add_argument(
        "--target",
        choices=("job", "run", "preview", "load"),
        default="job",
        help="Schema to print: 'job' input (default), 'run' or 'preview' output",
    )
    add_commands(subcommands, job_commands)
    add_recipe_commands(subcommands)
    add_studio_command(subcommands)
    add_suggest_commands(subcommands)
    return parser


def _run_job_command(args: argparse.Namespace) -> int:
    if args.job_command == "schema":
        payload = {
            "job": job_json_schema,
            "run": job_run_json_schema,
            "preview": job_preview_json_schema,
            "load": job_load_json_schema,
        }[args.target]()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return int(JobExitCode.SUCCESS)
    if args.job_command == "load":
        return load_job_command(args)
    if args.job_command == "validate":
        job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
        print(json.dumps(job_validation_summary(job), indent=2))
        return int(JobExitCode.SUCCESS)
    if args.job_command == "preview":
        preview = preview_job_file(args.file, allow_absolute_paths=args.allow_absolute_paths)
        print(
            preview.model_dump_json(indent=2)
            if args.output == "json"
            else render_preview_table(preview)
        )
        return int(preview.exit_code)
    report = run_job_file(args.file, allow_absolute_paths=args.allow_absolute_paths)
    print(report.model_dump_json(indent=2))
    return int(report.exit_code)


def _mcp_command(args: argparse.Namespace) -> int:
    try:
        from rastro.adapters.mcp_server import serve
    except ImportError:
        print(json.dumps(missing_extra_error("mcp")), file=sys.stderr)
        return int(JobExitCode.CONFIG_ERROR)
    if not args.root.is_dir():
        raise RastroError("--root must be an existing directory")
    serve(args.root)
    return int(JobExitCode.SUCCESS)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "mcp":
        return _mcp_command(args)
    if args.command == "recipe":
        return recipe_command(args)
    if args.command == "dataset":
        return dataset_command(args)
    if args.command == "studio":
        return studio_command(args)
    if args.command == "suggest":
        return suggest_command(args)
    if args.command == "new":
        return new_command(args)
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "rastro": __version__,
                    "python": sys.version.split()[0],
                    "dependencies": {
                        name: version(name) for name in ("pydantic", "scrapling", "structlog")
                    },
                    "capabilities": [
                        "offline_html",
                        "jsonld",
                        "css",
                        "pydantic_validation",
                        "provenance",
                        "declarative_jobs",
                    ],
                }
            )
        )
        return int(JobExitCode.SUCCESS)
    if args.command == "validate-recipe":
        with args.path.open("rb") as file:
            raw = file.read(65_537)
        if len(raw) > 65_536:
            raise RastroError("Recipe exceeds 64 KiB")
        print(Recipe.model_validate_json(raw).model_dump_json())
        return int(JobExitCode.SUCCESS)
    if args.command == "job":
        return _run_job_command(args)
    from rastro.cli_demo import demo_command

    return demo_command()


def _report_job_error(error: BaseException) -> int:
    print(json.dumps(classify(error)), file=sys.stderr)
    return int(JobExitCode.CONFIG_ERROR)


def _is_job_command(argv: list[str]) -> bool:
    return next((item for item in argv if not item.startswith("-")), None) in _JSON_ERRORS


def main(argv: list[str] | None = None) -> int:
    arguments = hoist_trust_flags(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(arguments)
    except UsageError as error:
        if _is_job_command(arguments):
            return _report_job_error(error)
        parser.print_usage(sys.stderr)
        parser.exit(int(JobExitCode.CONFIG_ERROR), f"{parser.prog}: error: {error}\n")
    handler: logging.StreamHandler[TextIO] | None = None
    logger = logging.getLogger("rastro")
    previous_level = logger.level
    if args.verbose:
        handler = logging.StreamHandler(sys.stderr)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    previous_trust = None
    try:
        previous_trust = apply_trust(args)
        return _dispatch(args)
    except (OSError, ValueError, ValidationError, SchemaError, RastroError) as error:
        if args.command in _JSON_ERRORS:
            return _report_job_error(error)
        payload = {
            "hint": "Check input, recipe, and resource limits",
            **classify(error),
            "error": "invalid_input",
        }
        print(json.dumps(payload), file=sys.stderr)
        return int(JobExitCode.CONFIG_ERROR)
    except Exception as error:
        if args.command not in _JSON_ERRORS:
            raise
        if handler is not None:
            logger.exception("Unexpected error while handling a job command")
        return _report_job_error(error)
    finally:
        if previous_trust is not None:
            set_process_trust(previous_trust)
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)


if __name__ == "__main__":
    raise SystemExit(main())
