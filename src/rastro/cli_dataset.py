"""Dataset CLI composition: store wiring, load, JSON/JSONL export and stale status."""

import argparse
import json
from contextlib import AbstractContextManager, nullcontext
from datetime import timedelta
from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.dataset_load import load_results, stale_sources
from rastro.application.drift import collect_snapshots
from rastro.application.job_loader import load_job, resolve_job_path
from rastro.application.jobs import run_job
from rastro.application.success_samples import collect_successes
from rastro.domain.drift import StructuralSnapshot
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
        parser.add_argument("--format", choices=("json", "jsonl"), default="json")


def load_job_command(args: argparse.Namespace) -> int:
    job = load_job(args.file, allow_absolute_paths=args.allow_absolute_paths)
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("job load requires a [dataset] block in the job file")
    path = resolve_job_path(
        spec.path, base_dir=args.file.parent, allow_absolute_paths=args.allow_absolute_paths
    )
    # Preserve symlink components for the adapter's refusal, after containment validation.
    raw_path = (
        Path(spec.path) if Path(spec.path).is_absolute() else args.file.parent.resolve() / spec.path
    )
    store = SQLiteDatasetStore(
        raw_path,
        limits=job.limits,
        near_match_fields=spec.match_fields,
        near_match_tolerance=timedelta(minutes=spec.match_tolerance_minutes or 30),
    )
    if path in [args.file.resolve()]:
        raise ConfigurationError("The dataset must be separate from the job file")
    monitor = SQLiteDriftMonitor(raw_path, limits=job.limits) if args.drift else None
    capture: AbstractContextManager[dict[str, StructuralSnapshot]]
    if args.drift:
        from rastro.adapters.drift_probe import ScraplingDriftProbe

        capture = collect_snapshots(ScraplingDriftProbe())
    else:
        capture = nullcontext({})
    from rastro.cli_repair_storage import serving_job

    effective_job = serving_job(job, args.file.parent, args.allow_absolute_paths)
    with capture as snapshots, collect_successes() as successes:
        report = run_job(
            effective_job, base_dir=args.file.parent, allow_absolute_paths=args.allow_absolute_paths
        )
    loaded = load_results(
        spec,
        [f.name for f in job.fields],
        report,
        store,
        rules=job.consistency,
        monitor=monitor,
        snapshots=snapshots,
    )
    from rastro.cli_repair_storage import retain_successes

    held_urls = {
        s.source_url
        for s in loaded.sources
        if s.consistency is not None and s.consistency.verdict == "inconsistent"
    }
    for result in report.results:
        if result.source_url in held_urls:
            successes.pop(result.job_id, None)
    issues = retain_successes(raw_path, spec.name, job.build_schema(), report, successes)
    loaded = loaded.model_copy(
        update={
            "sources": tuple(
                source.model_copy(update={"issues": issues.get(source.source_url, ())})
                for source in loaded.sources
            )
        }
    )
    print(loaded.model_dump_json(indent=2))
    return int(loaded.exit_code)


def dataset_command(args: argparse.Namespace) -> int:
    store = SQLiteDatasetStore(args.path)
    names = store.dataset_names()
    if args.dataset is None:
        if len(names) != 1:
            raise ConfigurationError("Choose --dataset from a file containing multiple datasets")
        args.dataset = names[0]
    if args.dataset not in names:
        raise ConfigurationError("Dataset name does not exist in this file")
    if args.dataset_command == "export":
        items = [r.model_dump(mode="json") for r in store.records(args.dataset)]
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
