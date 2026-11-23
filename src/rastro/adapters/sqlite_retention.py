"""Delete recipe versions that are past retention, keeping each key's highest version."""

from __future__ import annotations

import sqlite3

from rastro.adapters.sqlite_lifecycle import drop_orphan_events


def prune_versions(
    db: sqlite3.Connection, cutoff: str, *, keep_serving: bool, dry_run: bool
) -> int:
    """Delete (or with ``dry_run`` count) versions last successful before ``cutoff``."""
    where = (
        "last_success_at < ? AND version < "
        "(SELECT MAX(newest.version) FROM recipes AS newest WHERE newest.key = recipes.key)"
    ) + (" AND state NOT IN ('active', 'degraded')" if keep_serving else "")
    if dry_run:
        row = db.execute(f"SELECT COUNT(*) FROM recipes WHERE {where}", (cutoff,))
        return int(row.fetchone()[0])
    count = db.execute(f"DELETE FROM recipes WHERE {where}", (cutoff,)).rowcount
    if count:
        drop_orphan_events(db)
    return count
