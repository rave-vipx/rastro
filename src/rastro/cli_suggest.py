"""`rastro suggest` and `rastro new`: recipes and jobs from example values (INT-002, JOB-001)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rastro.application.job_loader import _read_local_source
from rastro.application.job_new import new_job
from rastro.application.job_preview import _cell
from rastro.application.model_choice import resolve_model
from rastro.application.suggest import suggest
from rastro.cli_model import add_model_options
from rastro.domain.job_fields import StringField
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.suggest import Suggestion
from rastro.ports.model import ModelPort

MAX_PREVIEW_ROWS = 50


def add_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    suggest_parser = subcommands.add_parser(
        "suggest", help="Suggest a CSS recipe from a saved page and example values"
    )
    suggest_parser.add_argument("page", type=Path, help="Saved HTML page")
    _add_example_options(suggest_parser)
    suggest_parser.add_argument("--output", choices=("json", "table"), default="json")
    new = subcommands.add_parser("new", help="Create a job file from a saved page and examples")
    new.add_argument("file", type=Path, help="New .toml job file; never overwritten")
    new.add_argument("--from", dest="page", type=Path, required=True, help="Saved HTML page")
    _add_example_options(new)
    new.add_argument("--dataset", help="Dataset path for approved records, relative to the job")
    new.add_argument("--identity", action="append", default=[], help="Record key field")
    new.add_argument(
        "--recipe-memory",
        metavar="FILE",
        help="Recipe store relative to the job, enabling repair (folder must exist)",
    )
    new.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write the job even if the recipe preview is only a partial success",
    )


def _add_example_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", help="Address the page was saved from")
    parser.add_argument(
        "--example",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Example value; repeating a field starts the next example record",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME",
        help="Field to extract without an example (requires --model)",
    )
    add_model_options(parser, "propose the recipe; examples become optional")
    parser.add_argument("--records-min", type=int, default=2, help="Minimum records (default 2)")


def parse_examples(items: list[str]) -> list[dict[str, str]]:
    """``FIELD=VALUE`` items as example records; a repeated field starts a new record."""
    if not items:
        return []
    records: list[dict[str, str]] = [{}]
    for item in items:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ConfigurationError("--example must look like FIELD=VALUE")
        if name in records[-1]:
            records.append({})
        records[-1][name] = value
    return records


def _model(args: argparse.Namespace) -> ModelPort | None:
    if not args.example and args.model is None:
        raise ConfigurationError("Give --example values, or --model with --field names")
    if args.field and args.model is None:
        raise ConfigurationError("--field without an example requires --model")
    return resolve_model(args.model, allow_remote=args.allow_remote)


def suggest_command(args: argparse.Namespace) -> int:
    model = _model(args)
    examples = parse_examples(args.example)
    html = _read_local_source(args.page, Limits().max_html_bytes, "html")
    names = dict.fromkeys([*(name for example in examples for name in example), *args.field])
    try:
        fields = [StringField(name=name) for name in names]
    except ValueError:
        raise ConfigurationError(
            "Example field names must start with a letter and use letters, digits or _"
        ) from None
    found = suggest(
        html,
        fields,
        examples=examples,
        url=args.url,
        records_min=args.records_min,
        model=model,
    )
    payload = suggestion_payload(found)
    if args.output == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload["recipe"], indent=2))
        print(_table(payload, list(names)))
    return 0


def new_command(args: argparse.Namespace) -> int:
    path = new_job(
        args.file,
        page=args.page,
        examples=parse_examples(args.example),
        url=args.url,
        records_min=args.records_min,
        dataset=args.dataset,
        identity=tuple(args.identity),
        recipe_memory=args.recipe_memory,
        allow_partial=args.allow_partial,
        fields=tuple(args.field),
        model=_model(args),
    )
    print(json.dumps({"schema_version": 1, "created": str(path)}))
    return 0


def suggestion_payload(found: Suggestion[Any]) -> dict[str, Any]:
    rows = [record.data.model_dump(mode="json") for record in found.preview.records]
    return {
        "schema_version": 1,
        "recipe": found.recipe.model_dump(mode="json"),
        "status": str(found.preview.status),
        "records": len(rows),
        "confidence": {k: v.model_dump(mode="json") for k, v in found.confidence.items()},
        "preview": rows[:MAX_PREVIEW_ROWS],
        "truncated": len(rows) > MAX_PREVIEW_ROWS,
    }


def _table(payload: dict[str, Any], names: list[str]) -> str:
    lines = [f"status: {payload['status']} | records: {payload['records']}", " | ".join(names)]
    lines += [" | ".join(_cell(row.get(name)) for name in names) for row in payload["preview"]]
    if payload["truncated"]:
        lines.append(f"(first {MAX_PREVIEW_ROWS} records shown)")
    return "\n".join(lines)
