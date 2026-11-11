"""Recipe retention CLI: prune old non-serving recipe versions (REC-005)."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rastro.domain.models import ConfigurationError


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    prune = commands.add_parser("prune", help="Delete old recipe versions that are not serving")
    prune.add_argument("--store", type=Path, required=True, help="Recipe memory SQLite file")
    prune.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    prune.add_argument("--dry-run", action="store_true", help="Count without deleting")


def prune_command(args: argparse.Namespace) -> int:
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    if not 0 <= args.older_than <= 36_500:
        raise ConfigurationError("--older-than must be between 0 and 36500 days")
    if args.store.is_symlink() or not args.store.is_file():
        raise ConfigurationError("--store must be an existing recipe memory file")
    cutoff = datetime.now(UTC) - timedelta(days=args.older_than)
    count = SQLiteRecipeRepository(args.store).prune(
        before=cutoff, keep_serving=True, dry_run=args.dry_run
    )
    key = "would_prune" if args.dry_run else "pruned"
    print(json.dumps({"schema_version": 1, "dry_run": args.dry_run, key: count}))
    return 0
