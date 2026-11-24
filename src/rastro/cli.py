"""Machine-readable CLI. Stdout is JSON; exit codes distinguish outcomes and config errors."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.metadata import version
from typing import TextIO

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
from rastro.application.model_budget import model_cost_override
from rastro.application.operator_trust import set_process_trust
from rastro.cli_agent import COMMANDS as AGENT_COMMANDS
from rastro.cli_agent import agent_command
from rastro.cli_config import COMMANDS as CONFIG_COMMANDS
from rastro.cli_config import config_command, template_command
from rastro.cli_dataset import dataset_command, load_job_command
from rastro.cli_errors import UsageError, classify, missing_extra_error
from rastro.cli_estimate import estimate_command
from rastro.cli_hook import hook_argv, notify, run_summary
from rastro.cli_hub_group import hub_group_command
from rastro.cli_model_setup import model_command
from rastro.cli_notify import notify_test_command
from rastro.cli_ops import JOB_COMMANDS as OPS_JOB_COMMANDS
from rastro.cli_ops import health_command
from rastro.cli_ops import job_command as ops_job_command
from rastro.cli_parser import build_parser as _build_parser
from rastro.cli_recipe import recipe_command
from rastro.cli_schedule import schedule_words_command, watch_job_command
from rastro.cli_shell import shell_command
from rastro.cli_stats import stats_command
from rastro.cli_studio import studio_command
from rastro.cli_suggest import new_command, suggest_command
from rastro.cli_trust import apply_trust, hoist_trust_flags, trust_options
from rastro.domain.health import job_health_json_schema
from rastro.domain.job_dataset import job_preview_json_schema
from rastro.domain.job_load import job_load_json_schema

# Commands whose errors are reported as one JSON object on stderr.
_JSON_ERRORS = frozenset(
    {"job", "recipe", "suggest", "new", "health", "hub", "shell", *AGENT_COMMANDS, *CONFIG_COMMANDS}
)


def _run_job_command(args: argparse.Namespace) -> int:
    if args.job_command == "schema":
        payload = {
            "job": job_json_schema,
            "run": job_run_json_schema,
            "preview": job_preview_json_schema,
            "load": job_load_json_schema,
            "health": job_health_json_schema,
        }[args.target]()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return int(JobExitCode.SUCCESS)
    handlers = {
        "stats": stats_command,
        "estimate": estimate_command,
        "template": template_command,
        "load": load_job_command,
        "watch": watch_job_command,
        "schedule": schedule_words_command,
    }
    if args.job_command in handlers:
        return handlers[args.job_command](args)
    if args.job_command in OPS_JOB_COMMANDS:
        return ops_job_command(args)
    if args.job_command == "validate":
        job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
        print(json.dumps(job_validation_summary(job), indent=2))
        return int(JobExitCode.SUCCESS)
    if args.job_command == "preview":
        preview = preview_job_file(
            args.file, allow_absolute_paths=args.allow_absolute_paths, **trust_options(args)
        )
        print(
            preview.model_dump_json(indent=2)
            if args.output == "json"
            else render_preview_table(preview)
        )
        return int(preview.exit_code)
    hook = hook_argv(args)
    report = run_job_file(
        args.file, allow_absolute_paths=args.allow_absolute_paths, **trust_options(args)
    )
    print(report.model_dump_json(indent=2), flush=True)
    notify(hook, run_summary(args.file, report))
    return int(report.exit_code)


def _mcp_command(args: argparse.Namespace) -> int:
    try:
        from rastro.adapters.mcp_server import serve
    except ImportError:
        print(json.dumps(missing_extra_error("mcp")), file=sys.stderr)
        return int(JobExitCode.CONFIG_ERROR)
    if not args.root.is_dir():
        raise RastroError("--root must be an existing directory")
    serve(args.root, allow_override_gates=args.allow_override_gates, **trust_options(args))
    return int(JobExitCode.SUCCESS)


def _dispatch(args: argparse.Namespace) -> int:
    with model_cost_override(
        getattr(args, "max_model_cost", None), getattr(args, "model_cost_per_call", None)
    ):
        return _run_command(args)


def _run_command(args: argparse.Namespace) -> int:
    handlers = {
        "mcp": _mcp_command,
        "shell": shell_command,
        "recipe": recipe_command,
        "dataset": dataset_command,
        "studio": studio_command,
        "model": model_command,
        "health": health_command,
        "hub": hub_group_command,
        "notify": lambda args: notify_test_command(args.channels),
        "suggest": suggest_command,
        "new": new_command,
        "job": _run_job_command,
    }
    if args.command in handlers:
        return handlers[args.command](args)
    if args.command in AGENT_COMMANDS:
        return agent_command(args)
    if args.command in CONFIG_COMMANDS:
        return config_command(args)
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
        previous_trust = apply_trust(args, arguments)
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
