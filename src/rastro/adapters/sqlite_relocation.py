"""Additive latest-success repair evidence in the dataset file (ADR 0019)."""

import sqlite3
from datetime import datetime
from pathlib import Path

from rastro.adapters.sqlite_dataset_schema import connection, safe
from rastro.adapters.structured import load_json
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.models import Limits, Outcome, Recipe, Source
from rastro.domain.repair import RelocationSnapshot, digest, recipe_digest
from rastro.mocklab.bundle import FixtureBundle
from rastro.mocklab.sanitize import assert_clean


def _schema(db: sqlite3.Connection, *, create: bool = False) -> bool:
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'relocation_*'"
        )
    }
    if tables:
        if tables != {"relocation_schema", "relocation_samples"} or db.execute(
            "SELECT version FROM relocation_schema WHERE id = 1"
        ).fetchall() != [(1,)]:
            raise DatasetStorageError("Foreign or newer relocation schema")
        return True
    if create:
        db.execute(
            "CREATE TABLE relocation_schema (id INTEGER PRIMARY KEY CHECK(id = 1), "
            "version INTEGER NOT NULL)"
        )
        db.execute("INSERT INTO relocation_schema VALUES (1, 1)")
        db.execute(
            "CREATE TABLE relocation_samples (dataset TEXT, source TEXT, schema TEXT, "
            "recipe TEXT, observed TEXT NOT NULL, snapshot TEXT, fixture TEXT NOT NULL, "
            "PRIMARY KEY(dataset, source, schema, recipe)) WITHOUT ROWID"
        )
    return create


class SQLiteRelocationStore:
    @safe
    def __init__(self, path: str | Path, *, limits: Limits | None = None) -> None:
        self._path = Path(validate_job_path_syntax(str(path))).absolute()
        self._limits = limits or Limits()

    @safe
    def save(
        self,
        dataset: str,
        source: Source,
        fixture: FixtureBundle,
        snapshot: RelocationSnapshot | None = None,
        *,
        observed_at: datetime,
    ) -> None:
        fixture = FixtureBundle.model_validate(load_json(fixture.model_dump_json(), self._limits))
        assert_clean(fixture.scan_view())
        manifest = fixture.manifest
        source_digest = digest(source.model_dump_json())
        if fixture.expected.status != Outcome.SUCCESS or not fixture.expected.records:
            raise DatasetStorageError("Only complete successful samples may be retained")
        # Replay origins are neutral; the separate key binds the original source.
        if snapshot is not None:
            snapshot = RelocationSnapshot.model_validate(
                load_json(snapshot.model_dump_json(), self._limits)
            )
            if (
                snapshot.recipe_digest != recipe_digest(manifest.recipe)
                or snapshot.schema_fingerprint != manifest.schema_fingerprint
            ):
                raise DatasetStorageError("Relocation snapshot does not match its fixture")
            if snapshot.source_digest != source_digest:
                raise DatasetStorageError("Relocation snapshot belongs to another source")
        from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
        from rastro.domain.memory import utc_timestamp

        SQLiteDatasetStore._validate(dataset, source.evidence_url, observed_at)
        observed = utc_timestamp(observed_at)
        key = (dataset, source_digest, manifest.schema_fingerprint, recipe_digest(manifest.recipe))
        with connection(self._path, self._limits, write=True) as db:
            _schema(db, create=True)
            previous = db.execute(
                "SELECT observed FROM relocation_samples WHERE dataset = ? "
                "AND source = ? AND schema = ? AND recipe = ?",
                key,
            ).fetchone()
            if previous and previous[0] > observed:
                observed = previous[0]  # The clock went backwards: clamp (docs/operations.md).
            db.execute(
                "INSERT OR REPLACE INTO relocation_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    observed,
                    snapshot.model_dump_json() if snapshot else None,
                    fixture.model_dump_json(),
                ),
            )

    @safe
    def latest(
        self, dataset: str, source: Source, fingerprint: str, recipe: Recipe
    ) -> tuple[RelocationSnapshot | None, FixtureBundle] | None:
        if not self._path.exists():
            return None
        with connection(self._path, self._limits) as db:
            if not _schema(db):
                return None
            row = db.execute(
                "SELECT snapshot, fixture FROM relocation_samples WHERE dataset = ? "
                "AND source = ? AND schema = ? AND recipe = ?",
                (dataset, digest(source.model_dump_json()), fingerprint, recipe_digest(recipe)),
            ).fetchone()
            if row is None:
                return None
            snapshot = (
                RelocationSnapshot.model_validate(load_json(row[0], self._limits))
                if row[0]
                else None
            )
            fixture = FixtureBundle.model_validate(load_json(row[1], self._limits))
            return snapshot, fixture
