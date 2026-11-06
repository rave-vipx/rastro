"""Atomic SQLite dataset snapshots, with short-lived connections and no deletion."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO

from rastro.adapters.sqlite_dataset_schema import migrate, safe, update_status
from rastro.adapters.structured import load_json
from rastro.domain.dataset import (
    ChangeKind,
    DatasetRecordKey,
    DatasetStorageError,
    ObservedRecord,
    RecordOrigin,
    SnapshotReport,
    SourceStatus,
    StoredRecord,
    UncertainMatch,
    near_match,
)
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.memory import utc_timestamp
from rastro.domain.sources import Limits, Source


class SQLiteDatasetStore:
    @safe
    def __init__(
        self,
        path: str | Path,
        *,
        near_match_fields: tuple[str, str, str] = ("title", "venue", "start_time"),
        near_match_tolerance: timedelta = timedelta(minutes=30),
        limits: Limits | None = None,
    ) -> None:
        self._path = Path(validate_job_path_syntax(str(path))).absolute()
        if near_match_tolerance < timedelta(0) or len(near_match_fields) != 3:
            raise DatasetStorageError("Invalid near-match configuration")
        self._match_fields, self._tolerance, self._limits = (
            near_match_fields,
            near_match_tolerance,
            limits or Limits(),
        )

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if any(p.is_symlink() for p in (self._path, *self._path.parents)):
            raise DatasetStorageError("Dataset paths must not contain symlinks")
        with closing(sqlite3.connect(self._path, timeout=5, isolation_level=None)) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self._limits.max_json_bytes)
            db.execute("PRAGMA trusted_schema = OFF")
            db.execute("PRAGMA secure_delete = ON")
            migrate(db)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _read(self, db: sqlite3.Connection, dataset: str) -> dict[str, StoredRecord]:
        rows: dict[str, StoredRecord] = {}
        size = 0
        cursor = db.execute(
            "SELECT key, payload FROM dataset_records WHERE dataset = ? ORDER BY key LIMIT ?",
            (dataset, self._limits.max_records + 1),
        )
        for key, payload in cursor:
            size += len(payload.encode())
            if len(rows) >= self._limits.max_records or size > self._limits.max_json_bytes:
                raise DatasetStorageError("Dataset exceeds configured limits")
            rows[key] = StoredRecord.model_validate(load_json(payload, self._limits))
        return rows

    @staticmethod
    def _validate(dataset: str, source: str, at: datetime) -> None:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        utc_timestamp(at)
        if Source(url=source).evidence_url != source:
            raise DatasetStorageError("Dataset sources require canonical evidence URLs")

    @safe
    def apply_snapshot(
        self,
        dataset: str,
        source_url: str,
        records: Sequence[ObservedRecord],
        *,
        complete: bool,
        observed_at: datetime,
    ) -> SnapshotReport:
        self._validate(dataset, source_url, observed_at)
        if type(complete) is not bool or len(records) > self._limits.max_records:
            raise DatasetStorageError("Invalid snapshot completeness or record count")
        incoming = self._prepare(dataset, source_url, records, observed_at)
        changes: dict[ChangeKind, list[DatasetRecordKey]] = {kind: [] for kind in ChangeKind}
        with self._connection(write=True) as db:
            rows = self._read(db, dataset)
            update_status(
                db,
                dataset,
                source_url,
                observed_at,
                complete,
                None if complete else "partial_snapshot",
            )
            for key, row in rows.items():
                if source_url in row.origins and key not in incoming:
                    changes[ChangeKind.MISSING if complete else ChangeKind.UNCERTAIN].append(
                        row.key
                    )
            for key, record in incoming.items():
                old = rows.get(key)
                kind = (
                    ChangeKind.CREATED
                    if old is None
                    else ChangeKind.UNCHANGED
                    if old.origins.get(source_url, old).fields == record.fields
                    else ChangeKind.UPDATED
                )
                changes[kind].append(record.key)
                rows[key] = self._observe(old, record)
            self._link_matches(rows, incoming)
            payloads = [(dataset, key, row.model_dump_json()) for key, row in sorted(rows.items())]
            if (
                len(rows) > self._limits.max_records
                or sum(len(p.encode()) for _, _, p in payloads) > self._limits.max_json_bytes
            ):
                raise DatasetStorageError("Dataset exceeds configured limits")
            for _, _, payload in payloads:
                load_json(payload, self._limits)
            db.executemany("INSERT OR REPLACE INTO dataset_records VALUES (?, ?, ?)", payloads)
        return SnapshotReport(keys={kind: tuple(keys) for kind, keys in changes.items()})

    def _prepare(
        self,
        dataset: str,
        source_url: str,
        records: Sequence[ObservedRecord],
        observed_at: datetime,
    ) -> dict[str, ObservedRecord]:
        incoming = {}
        total_bytes = 0
        for record in records:
            payload = json.dumps(record.model_dump(mode="json"), allow_nan=False)
            total_bytes += len(payload.encode())
            if total_bytes > self._limits.max_json_bytes:
                raise DatasetStorageError("Observation exceeds configured limits")
            record = ObservedRecord.model_validate(load_json(payload, self._limits))
            key = record.key.model_dump_json()
            if (
                record.key.dataset != dataset
                or record.source_url != source_url
                or record.observed_at != observed_at
                or key in incoming
            ):
                raise DatasetStorageError("Snapshot identity, origin or observation mismatch")
            incoming[key] = record
        return incoming

    @staticmethod
    def _observe(old: StoredRecord | None, record: ObservedRecord) -> StoredRecord:
        origins = dict(old.origins) if old else {}
        prior = origins.get(record.source_url)
        origin = RecordOrigin(
            **record.model_dump(),
            first_observed_at=prior.first_observed_at if prior else record.observed_at,
            last_observed_at=record.observed_at,
        )
        origins[record.source_url] = origin
        latest = max(origins.values(), key=lambda item: (item.observed_at, item.source_url))
        return StoredRecord(
            **latest.model_dump(exclude={"first_observed_at", "last_observed_at"}),
            first_observed_at=min(o.first_observed_at for o in origins.values()),
            last_observed_at=latest.observed_at,
            origins=origins,
            uncertain_match=old.uncertain_match if old else (),
        )

    def _link_matches(
        self, rows: dict[str, StoredRecord], incoming: dict[str, ObservedRecord]
    ) -> None:
        # shortcut: bounded origin scan; add an index when larger datasets are supported.
        for key, record in incoming.items():
            for other_key, other in tuple(rows.items()):
                for origin in other.origins.values():
                    if near_match(record, origin, self._match_fields, self._tolerance):
                        pair = sorted(
                            (record, origin), key=lambda r: (r.key.model_dump_json(), r.source_url)
                        )
                        marker = UncertainMatch(
                            keys=(pair[0].key, pair[1].key),
                            origins=(pair[0].source_url, pair[1].source_url),
                        )
                        for target in (key, other_key):
                            row = rows[target]
                            if marker not in row.uncertain_match:
                                rows[target] = row.model_copy(
                                    update={"uncertain_match": (*row.uncertain_match, marker)}
                                )

    @safe
    def mark_source_unavailable(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str
    ) -> None:
        self._validate(dataset, source_url, observed_at)
        reason = check_reason(reason)
        with self._connection(write=True) as db:
            update_status(db, dataset, source_url, observed_at, False, reason)

    @safe
    def records(self, dataset: str) -> tuple[StoredRecord, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with self._connection() as db:
            return tuple(self._read(db, dataset).values())

    @safe
    def export_jsonl(self, dataset: str, stream: TextIO) -> None:
        for record in self.records(dataset):
            stream.write(record.model_dump_json() + "\n")

    @safe
    def source_status(self, dataset: str) -> tuple[SourceStatus, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with self._connection() as db:
            return tuple(
                SourceStatus.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM dataset_sources WHERE dataset = ? ORDER BY source",
                    (dataset,),
                )
            )
