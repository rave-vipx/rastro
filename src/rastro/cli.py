"""Machine-readable CLI. Stdout is JSON; exit codes distinguish outcomes and config errors."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn, TextIO

from pydantic import BaseModel, Field, ValidationError
from pydantic_core import SchemaError

from rastro import (
    JobExitCode,
    JsonLdRecipe,
    Outcome,
    Rastro,
    RastroError,
    Recipe,
    Source,
    __version__,
    job_json_schema,
    job_run_json_schema,
    load_job,
    run_job_file,
    schema_fingerprint,
)
from rastro.cli_errors import UsageError, classify


class _DemoProduct(BaseModel):
    name: str = Field(min_length=1)
    price: float = Field(ge=0, allow_inf_nan=False)


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
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="Run a synthetic JSON-LD extraction without network access")
    subcommands.add_parser("doctor", help="Report installed core versions and capabilities as JSON")
    validate = subcommands.add_parser("validate-recipe", help="Validate a local declarative recipe")
    validate.add_argument("path", type=Path)

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

    job_schema = job_commands.add_parser("schema", help="Print JSON Schema for declarative jobs")
    job_schema.add_argument(
        "--target",
        choices=("job", "run"),
        default="job",
        help="Schema to print: 'job' input format (default) or 'run' output format",
    )
    return parser


def _run_job_command(args: argparse.Namespace) -> int:
    if args.job_command == "schema":
        payload = job_json_schema() if args.target == "job" else job_run_json_schema()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return int(JobExitCode.SUCCESS)
    if args.job_command == "validate":
        job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
        print(
            json.dumps(
                {
                    "valid": True,
                    "format_version": job.format_version,
                    "schema_fingerprint": schema_fingerprint(job.build_schema()),
                    "source_count": len(job.sources),
                    "fields": [field.name for field in job.fields],
                    "job": job.redacted_dump(),
                },
                indent=2,
            )
        )
        return int(JobExitCode.SUCCESS)
    report = run_job_file(args.file, allow_absolute_paths=args.allow_absolute_paths)
    print(report.model_dump_json(indent=2))
    return int(report.exit_code)


def _dispatch(args: argparse.Namespace) -> int:
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
    result = Rastro().extract_html(
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Trail notebook","offers":{"price":12.5}}'
        "</script>",
        source=Source(url="https://example.com/products", locale="en-US"),
        schema=_DemoProduct,
        recipe=JsonLdRecipe(
            item_type="Product", fields={"name": "/name", "price": "/offers/price"}
        ),
    )
    print(result.model_dump_json(indent=2))
    return int(JobExitCode.SUCCESS if result.status == Outcome.SUCCESS else JobExitCode.FAILED)


def _report_job_error(error: BaseException) -> int:
    print(json.dumps(classify(error)), file=sys.stderr)
    return int(JobExitCode.CONFIG_ERROR)


def _is_job_command(argv: list[str]) -> bool:
    return next((item for item in argv if not item.startswith("-")), None) == "job"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
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
    try:
        return _dispatch(args)
    except (OSError, ValueError, ValidationError, SchemaError, RastroError) as error:
        if args.command == "job":
            return _report_job_error(error)
        hint = "Check input, recipe, and resource limits"
        print(json.dumps({"error": "invalid_input", "hint": hint}), file=sys.stderr)
        return int(JobExitCode.CONFIG_ERROR)
    except Exception as error:
        if args.command != "job":
            raise
        if handler is not None:
            logger.exception("Unexpected error while handling a job command")
        return _report_job_error(error)
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)


if __name__ == "__main__":
    raise SystemExit(main())
