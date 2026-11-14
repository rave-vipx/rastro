"""Recipe lifecycle CLI: repair, re-evaluated promotion, rollback, comparison and apply."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rastro.application.job_repair import promote_job_recipe, repair_job_sources
from rastro.application.recipe_apply import write_serving_recipe
from rastro.application.repair_context import RepairContext, safe_path
from rastro.cli_hub import add_commands as add_hub_commands
from rastro.cli_hub import hub_command
from rastro.cli_model import add_model_options
from rastro.cli_prune import add_commands as add_prune_commands
from rastro.cli_prune import prune_command
from rastro.cli_trust import add_trust_arguments, trust_options
from rastro.domain.models import ConfigurationError
from rastro.domain.recipe_compare import RecipeComparison, RepairReport

_HELP = {
    "repair": "Propose, evaluate and store repair candidates for broken sources",
    "promote": "Re-check a stored candidate on the current page and activate it if it passes",
    "rollback": "Make a version that served before active again",
    "compare": "Show which fields and selectors differ between two stored versions",
    "apply": "Write the serving recipe from recipe_memory back into the job file",
    "show": "Print a stored recipe version as JSON (read-only)",
}


def add_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    summary = (
        "Manage stored recipes: repair, promote, rollback, compare, apply, show, prune, "
        "export and import"
    )
    parser = subcommands.add_parser("recipe", help=summary, description=summary)
    commands = parser.add_subparsers(dest="recipe_command", required=True)
    add_hub_commands(commands)
    add_prune_commands(commands)
    for name in ("repair", "promote", "rollback", "compare", "apply", "show"):
        command = commands.add_parser(name, help=_HELP[name], description=_HELP[name])
        command.add_argument("file", type=Path)
        command.add_argument(
            "--source",
            type=int,
            default=None if name == "repair" else 1,
            help="One-based source index; repair defaults to all sources",
        )
        command.add_argument("--allow-absolute-paths", action="store_true")
        if name in ("promote", "rollback"):
            command.add_argument("--version", type=int, required=True)
            command.add_argument("--reason")
        if name in ("repair", "promote"):
            add_trust_arguments(command)
        if name == "promote":
            command.add_argument(
                "--override-gates",
                action="store_true",
                help="Activate a quarantined candidate; requires --reason",
            )
        if name == "show":
            command.add_argument("--version", type=int, help="Stored version (default: serving)")
        if name == "compare":
            command.add_argument("--from-version", type=int, required=True)
            command.add_argument("--to-version", type=int, required=True)
        if name == "repair":
            command.add_argument("--report", help="New report path inside the job directory")
            add_model_options(command, "propose repairs relocation cannot find and judge them")


def recipe_command(args: argparse.Namespace) -> int:
    if args.recipe_command in {"export", "import"}:
        return hub_command(args)
    if args.recipe_command == "prune":
        return prune_command(args)
    if args.recipe_command == "apply":
        applied = write_serving_recipe(
            args.file, args.source, allow_absolute_paths=args.allow_absolute_paths
        )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "path": str(applied.path),
                    "source_index": applied.source_index,
                    "recipe_version": applied.recipe_version,
                    "changed": applied.changed,
                }
            )
        )
        return 0
    context = RepairContext.load(args.file, args.allow_absolute_paths)
    if args.recipe_command == "show":
        _, key = context.locate(args.source)
        stored = context.repository.get(key, version=args.version)
        if stored is None:
            raise ConfigurationError("No stored recipe for this source and version")
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_index": args.source,
                    "version": stored.recipe.version,
                    "state": str(stored.metadata.state),
                    "recipe": stored.recipe.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 0
    if args.recipe_command == "compare":
        _, key, _ = context.source(args.source)
        old, new = (
            context.repository.get(key, version=v) for v in (args.from_version, args.to_version)
        )
        if old is None or new is None:
            raise ConfigurationError("Comparison recipe version does not exist")
        print(RecipeComparison.between(old.recipe, new.recipe).model_dump_json(indent=2))
        return 0
    if args.recipe_command == "rollback":
        _, key, _ = context.source(args.source)
        stored = context.repository.rollback(
            key, args.version, reason=_reason(args), at=datetime.now(UTC)
        )
        context.serving_changed(args.source)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": stored.metadata.state,
                    "version": stored.recipe.version,
                }
            )
        )
        return 0
    if args.recipe_command == "promote":
        return _print(
            promote_job_recipe(
                args.file,
                args.source,
                args.version,
                reason=_reason(args),
                override_gates=args.override_gates,
                allow_absolute_paths=args.allow_absolute_paths,
                **trust_options(args),
            )
        )
    return _repair_command(context, args)


def _reason(args: argparse.Namespace) -> str:
    if args.reason is not None:
        return str(args.reason)
    if getattr(args, "override_gates", False):
        raise ConfigurationError("--override-gates requires an explicit --reason")
    return "Operator requested lifecycle action"


def _print(report: RepairReport) -> int:
    print(report.model_dump_json(indent=2))
    return report.exit_code


def _repair_command(context: RepairContext, args: argparse.Namespace) -> int:
    # Reserve output before lifecycle writes; exclusive creation refuses existing files/symlinks.
    report_path = safe_path(
        args.report or f"repair-report-{uuid4()}.json", context.base, context.allow
    )
    descriptor: int | None = os.open(
        report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        report = repair_job_sources(
            args.file,
            None if args.source is None else (args.source,),
            allow_absolute_paths=args.allow_absolute_paths,
            model=args.model,
            allow_remote=args.allow_remote,
            **trust_options(args),
        )
        assert descriptor is not None
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = None
            output.write(report.model_dump_json(indent=2) + "\n")
        return _print(report)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        report_path.unlink(missing_ok=True)
        raise
