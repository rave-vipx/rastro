"""Dataset CLI: job load, JSON/JSONL/HTML export and stale status."""

import argparse
import json
import sys
from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_dataset_admin import migrate_dataset
from rastro.adapters.sqlite_dataset_schema import SCHEMA_VERSION
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.dataset_load import stale_sources
from rastro.application.errors import RunInProgressError
from rastro.application.job_dataset import load_job_file
from rastro.cli_dataset_export import html_export, select_records
from rastro.cli_errors import classify
from rastro.cli_hook import add_hook_argument, hook_argv, load_summary, notify
from rastro.cli_trust import add_trust_arguments, trust_options
from rastro.domain.job_results import JobExitCode
from rastro.domain.models import ConfigurationError

_DATASET_HELP = {
    "export": "Print a dataset's records as JSON, JSONL or an HTML table",
    "status": "Show each source's freshness and last load",
    "migrate": "Migrate a dataset SQLite file forward to a schema version",
}


def add_commands(subcommands: argparse._SubParsersAction, jobs: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    load = jobs.add_parser("load", help="Run a job and load approved observed fields")
    load.add_argument("file", type=Path, help="Job file (.toml or .json)")
    load.add_argument("--drift", action="store_true", help="Measure and report structural drift")
    load.add_argument(
        "--output", choices=("json",), default="json", help="Machine-readable output format"
    )
    load.add_argument(
        "--allow-absolute-paths", action="store_true", help="Allow absolute paths in the job file"
    )
    add_trust_arguments(load)
    add_hook_argument(load)
    dataset = subcommands.add_parser("dataset", help="Read dataset records and freshness")
    commands = dataset.add_subparsers(dest="dataset_command", required=True)
    for command in ("export", "status"):
        parser = commands.add_parser(command, help=_DATASET_HELP[command])
        parser.add_argument("path", type=Path, help="Dataset SQLite file")
        parser.add_argument(
            "--dataset", help="Dataset name (required if file contains multiple datasets)"
        )
        parser.add_argument(
            "--format",
            choices=("json", "jsonl", "html") if command == "export" else ("json", "jsonl"),
            default="json",
            help="Output format",
        )
        if command == "export":
            parser.add_argument(
                "--sort-field", help="Field to sort records by (ISO date for --upcoming)"
            )
            parser.add_argument(
                "--upcoming", action="store_true", help="Only records dated today or later"
            )
            parser.add_argument(
                "--timezone", default="UTC", help="IANA timezone that decides today"
            )
    migrate = commands.add_parser("migrate", help=_DATASET_HELP["migrate"])
    migrate.add_argument("path", type=Path, help="Dataset SQLite file")
    migrate.add_argument(
        "--to",
        type=int,
        default=SCHEMA_VERSION,
        help="Target dataset schema version",
    )


def load_job_command(args: argparse.Namespace) -> int:
    hook = hook_argv(args)
    try:
        loaded = load_job_file(
            args.file,
            drift=args.drift,
            allow_absolute_paths=args.allow_absolute_paths,
            **trust_options(args),
        )
    except RunInProgressError as error:
        print(json.dumps(classify(error)), file=sys.stderr)
        return int(JobExitCode.NEEDS_ATTENTION)
    print(loaded.model_dump_json(indent=2), flush=True)
    notify(hook, load_summary(args.file, loaded))
    return int(loaded.exit_code)


def dataset_command(args: argparse.Namespace) -> int:
    if not args.path.exists():
        raise ConfigurationError(
            f"Dataset file not found: {args.path.as_posix()}",
            location="path",
            hint="Create it with `rastro job load <job file>` or check the path",
        )
    if args.dataset_command == "migrate":
        print(json.dumps(migrate_dataset(args.path, to=args.to), indent=2, ensure_ascii=True))
        return 0
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
        return _status(store, args)
    label = {"schema_version": 1, "data_trust": "untrusted_source_data"}
    if args.format == "jsonl":
        for item in items:
            print(json.dumps({**label, **item}, ensure_ascii=True))
    else:
        payload = {**label, "dataset": args.dataset, "records": items}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def _status(store: SQLiteDatasetStore, args: argparse.Namespace) -> int:
    """Exit 4 (needs attention) when a source is stale or has open drift (DATA-004)."""
    label = {"schema_version": 1, "data_trust": "untrusted_source_data"}
    statuses = store.source_status(args.dataset)
    stale = stale_sources(store, args.dataset)
    drift = SQLiteDriftMonitor(args.path).open_drift(args.dataset)
    if args.format == "jsonl":
        for status in statuses:
            print(json.dumps({**label, **status.model_dump(mode="json")}, ensure_ascii=True))
    else:
        payload = {
            **label,
            "dataset": args.dataset,
            "sources": [s.model_dump(mode="json") for s in statuses],
            "stale_sources": [s.model_dump(mode="json") for s in stale],
            "open_drift": [d.model_dump(mode="json") for d in drift],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return int(JobExitCode.NEEDS_ATTENTION if stale or drift else JobExitCode.SUCCESS)
