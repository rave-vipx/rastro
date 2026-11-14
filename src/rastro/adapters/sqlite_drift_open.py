"""Open-drift and rebaseline rows of drift schema 2 (ADR 0022); callers own the transaction.

``drift_rebase`` marks a source whose serving recipe an operator changed (promotion or
rollback): its next successful run becomes the new baseline without opening drift.
"""

import sqlite3

from rastro.domain.drift import DriftAssessment, StructuralSnapshot
from rastro.domain.health import OpenDrift
from rastro.domain.memory import utc_timestamp


def create_table(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE drift_open (dataset TEXT, context TEXT, source TEXT NOT NULL, "
        "run TEXT NOT NULL, baseline_run TEXT, observed TEXT NOT NULL, payload TEXT NOT NULL, "
        "PRIMARY KEY(dataset, context)) WITHOUT ROWID"
    )
    db.execute(
        "CREATE TABLE drift_rebase (dataset TEXT, source TEXT, PRIMARY KEY(dataset, source)) "
        "WITHOUT ROWID"
    )


def mark_rebase(db: sqlite3.Connection, dataset: str, source: str) -> None:
    db.execute("INSERT OR IGNORE INTO drift_rebase VALUES (?, ?)", (dataset, source))


def take_rebase(db: sqlite3.Connection, dataset: str, source: str) -> bool:
    return (
        db.execute(
            "DELETE FROM drift_rebase WHERE dataset = ? AND source = ?", (dataset, source)
        ).rowcount
        > 0
    )


def pinned(db: sqlite3.Connection, dataset: str, context: str) -> tuple[str | None] | None:
    """``(baseline_run,)`` while drift is open in this context, otherwise ``None``."""
    row = db.execute(
        "SELECT baseline_run FROM drift_open WHERE dataset = ? AND context = ?",
        (dataset, context),
    ).fetchone()
    return (row[0],) if row else None


def open_drift(
    db: sqlite3.Connection,
    dataset: str,
    context: str,
    snapshot: StructuralSnapshot,
    assessment: DriftAssessment,
    baseline_run: str | None,
) -> None:
    if assessment.risk == "low":
        return
    item = OpenDrift(
        source_url=snapshot.source_url,
        run_id=snapshot.run_id,
        baseline_run=baseline_run,
        observed_at=snapshot.observed_at,
        risk=assessment.risk,
        score=assessment.score,
        verdict=assessment.verdict,
    )
    db.execute(
        "INSERT INTO drift_open VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            dataset,
            context,
            item.source_url,
            item.run_id,
            baseline_run,
            utc_timestamp(snapshot.observed_at),
            item.model_dump_json(),
        ),
    )


def read(db: sqlite3.Connection, dataset: str) -> tuple[OpenDrift, ...]:
    return tuple(
        OpenDrift.model_validate_json(row[0])
        for row in db.execute(
            "SELECT payload FROM drift_open WHERE dataset = ? ORDER BY source, observed, context",
            (dataset,),
        )
    )


def clear(db: sqlite3.Connection, dataset: str, source: str) -> int:
    return db.execute(
        "DELETE FROM drift_open WHERE dataset = ? AND source = ?", (dataset, source)
    ).rowcount
