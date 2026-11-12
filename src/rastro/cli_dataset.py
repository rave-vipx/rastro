"""Dataset CLI: job load, JSON/JSONL/HTML export and stale status."""

import argparse
import json
from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.dataset_load import stale_sources
from rastro.application.job_dataset import load_job_file
from rastro.cli_dataset_export import html_export, select_records
from rastro.domain.models import ConfigurationError


def add_commands(subcommands: argparse._SubParsersAction, jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    load = jobs.add_parser("load", help="Run a job and load approved observed fields")
    load.add_argument("file", type=Path)
    load.add_argument("--drift", action="store_true", help="Measure and report structural drift")
    load.add_argument("--output", choices=("json",), default="json")
    load.add_argument("--allow-absolute-paths", action="store_true")
    dataset = subcommands.add_parser("dataset", help="Read dataset records and freshness")
    commands = dataset.add_subparsers(dest="dataset_command", required=True)
    for command in ("export", "status"):
        parser = commands.add_parser(command)
        parser.add_argument("path", type=Path)
        parser.add_argument(
            "--dataset", help="Dataset name (required if file contains multiple datasets)"
        )
        parser.add_argument(
            "--format",
            choices=("json", "jsonl", "html") if command == "export" else ("json", "jsonl"),
            default="json",
        )
        if command == "export":
            parser.add_argument("--sort-field")
            parser.add_argument("--upcoming", action="store_true")
            parser.add_argument("--timezone", default="UTC")


def load_job_command(args: argparse.Namespace) -> int:
    loaded = load_job_file(
        args.file, drift=args.drift, allow_absolute_paths=args.allow_absolute_paths
    )
    print(loaded.model_dump_json(indent=2))
    return int(loaded.exit_code)


def dataset_command(args: argparse.Namespace) -> int:
    if not args.path.exists():
        raise ConfigurationError(
            f"Dataset file not found: {args.path.as_posix()}",
            location="path",
            hint="Create it with `rastro job load <job file>` or check the path",
        )
    store = SQLiteDatasetStore(args.path)
    names = store.dataset_names()
    if args.dataset is None:
        if len(names) != 1:
            raise ConfigurationError("Choose --dataset from a file containing multiple datasets")
        args.dataset = names[0]
    if args.dataset not in names:
        raise ConfigurationError("Dataset name does not exist in this file")
    if args.dataset_command == "export":
        all_records = store.records(args.dataset)
        records = select_records(
            all_records, args.sort_field, args.upcoming, timezone=args.timezone
        )
        if args.format == "html":
            stale = {
                key for source in stale_sources(store, args.dataset) for key in source.affected_keys
            }
            fields = list(dict.fromkeys(name for record in all_records for name in record.fields))
            print(html_export(records, stale, fields))
            return 0
        items = [r.model_dump(mode="json") for r in records]
    else:
        items = [s.model_dump(mode="json") for s in store.source_status(args.dataset)]
    label = {"schema_version": 1, "data_trust": "untrusted_source_data"}
    if args.format == "jsonl":
        for item in items:
            print(json.dumps({**label, **item}, ensure_ascii=True))
    else:
        payload = {
            **label,
            "dataset": args.dataset,
            "records" if args.dataset_command == "export" else "sources": items,
        }
        if args.dataset_command == "status":
            payload["stale_sources"] = [
                s.model_dump(mode="json") for s in stale_sources(store, args.dataset)
            ]
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0
