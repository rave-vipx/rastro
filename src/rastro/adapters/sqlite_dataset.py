"""Atomic SQLite dataset snapshots, with short-lived connections and no deletion."""

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TextIO

from rastro.adapters.dataset_snapshot import DatasetSnapshot
from rastro.adapters.sqlite_dataset_schema import (
    connection,
    has_validators,
    last_attempt,
    safe,
    update_status,
)
from rastro.adapters.structured import load_json
from rastro.domain.dataset import (
    DatasetNotFoundError,
    DatasetRecordKey,
    DatasetStorageError,
    ObservedRecord,
    SnapshotReport,
    SourceStatus,
    StoredRecord,
)
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.sources import Limits, Source


class SQLiteDatasetStore(DatasetSnapshot):
    @safe
    def __init__(
        self,
        path: str | Path,
        *,
        near_match_fields: tuple[str, str, str] | None = ("title", "venue", "start_time"),
        near_match_tolerance: timedelta = timedelta(minutes=30),
        limits: Limits | None = None,
    ) -> None:
        self._path = Path(validate_job_path_syntax(str(path))).absolute()
        if near_match_tolerance < timedelta(0) or (
            near_match_fields is not None and len(near_match_fields) != 3
        ):
            raise DatasetStorageError("Invalid near-match configuration")
        self._match_fields, self._tolerance, self._limits = (
            near_match_fields,
            near_match_tolerance,
            limits or Limits(),
        )

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
        with connection(self._path, self._limits, write=True) as db:
            last = last_attempt(db, dataset, source_url)
            if last is not None and observed_at < last:
                # The clock went backwards: clamp so observation times never regress.
                observed_at = last
                incoming = {
                    key: item.model_copy(update={"observed_at": last})
                    for key, item in incoming.items()
                }
            rows = self._read(db, dataset)
            update_status(
                db,
                dataset,
                source_url,
                observed_at,
                complete,
                None if complete else "partial_snapshot",
            )
            report = self._classify(rows, incoming, source_url, complete=complete)
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
        return report

    @safe
    def mark_source_unavailable(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str
    ) -> None:
        self.record_source_failure(dataset, source_url, observed_at, reason, None)

    @safe
    def record_source_failure(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str, detail: str | None
    ) -> None:
        """``mark_source_unavailable`` plus a content-free detail (``SourceFailureRecorder``)."""
        self._validate(dataset, source_url, observed_at)
        reason = check_reason(reason)
        detail = None if detail is None else check_reason(detail)
        with connection(self._path, self._limits, write=True) as db:
            update_status(db, dataset, source_url, observed_at, False, reason, detail)

    @safe
    def records(self, dataset: str) -> tuple[StoredRecord, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        if not self._path.exists() and not any(
            p.is_symlink() for p in (self._path, *self._path.parents)
        ):
            raise DatasetNotFoundError("Dataset file does not exist")
        with connection(self._path, self._limits) as db:
            return tuple(self._read(db, dataset).values())

    @safe
    def export_jsonl(self, dataset: str, stream: TextIO) -> None:
        for record in self.records(dataset):
            stream.write(record.model_dump_json() + "\n")

    @safe
    def source_status(self, dataset: str) -> tuple[SourceStatus, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with connection(self._path, self._limits) as db:
            return tuple(
                SourceStatus.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM dataset_sources WHERE dataset = ? ORDER BY source",
                    (dataset,),
                )
            )

    @safe
    def dataset_names(self) -> tuple[str, ...]:
        """Names in an existing dataset file; reading never creates a database."""
        with connection(self._path, self._limits) as db:
            return tuple(
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT dataset FROM dataset_sources ORDER BY dataset"
                )
            )

    @safe
    def get_validators(self, dataset: str, source_url: str) -> dict[str, str]:
        self._validate(dataset, Source(url=source_url).evidence_url, datetime.now().astimezone())
        if not self._path.exists() and not any(
            p.is_symlink() for p in (self._path, *self._path.parents)
        ):
            return {}
        with connection(self._path, self._limits) as db:
            if not has_validators(db):
                return {}
            row = db.execute(
                "SELECT payload, origin, observed_at FROM dataset_validators "
                "WHERE dataset = ? AND source = ?",
                (dataset, sha256(source_url.encode()).hexdigest()),
            ).fetchone()
            values = self._validators(load_json(row[0], self._limits)) if row else {}
            origin = row[1] if row else Source(url=source_url).evidence_url
            status = db.execute(
                "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
                (dataset, origin),
            ).fetchone()
            success = SourceStatus.model_validate_json(status[0]).last_success if status else None
            if success is None or row is None or success.isoformat() != row[2]:
                return {}
        return values

    @staticmethod
    def _validators(values: object) -> dict[str, str]:
        if not isinstance(values, dict) or any(
            key not in {"etag", "last-modified", "_target", "_source"}
            or not isinstance(value, str)
            or not 0 < len(value.encode()) <= 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or (key == "_target" and re.fullmatch(r"[0-9a-f]{64}", value) is None)
            or (key == "_source" and Source(url=value).evidence_url != value)
            for key, value in values.items()
        ):
            raise DatasetStorageError("Invalid HTTP validators")
        return dict(values)

    @safe
    def save_validators(
        self,
        dataset: str,
        source_url: str,
        validators: dict[str, str],
        *,
        observed_at: datetime | None = None,
    ) -> None:
        self._validate(dataset, Source(url=source_url).evidence_url, datetime.now().astimezone())
        payload = json.dumps(self._validators(validators))
        origin = validators.get("_source", Source(url=source_url).evidence_url)
        with connection(self._path, self._limits, write=True) as db:
            row = db.execute(
                "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
                (dataset, origin),
            ).fetchone()
            success = SourceStatus.model_validate_json(row[0]).last_success if row else None
            if success is None or (observed_at is not None and observed_at != success):
                return
            db.execute(
                "INSERT OR REPLACE INTO dataset_validators VALUES (?, ?, ?, ?, ?)",
                (
                    dataset,
                    sha256(source_url.encode()).hexdigest(),
                    origin,
                    success.isoformat(),
                    payload,
                ),
            )

    @safe
    def mark_source_unchanged(self, dataset: str, source_url: str, observed_at: datetime) -> None:
        self._validate(dataset, source_url, observed_at)
        with connection(self._path, self._limits, write=True) as db:
            row = db.execute(
                "SELECT payload FROM dataset_sources WHERE dataset = ? AND source = ?",
                (dataset, source_url),
            ).fetchone()
            old = SourceStatus.model_validate_json(row[0]) if row else None
            if old is None or old.last_success is None:
                raise DatasetStorageError("Unchanged source requires a previous load")
            observed_at = max(old.last_attempt, observed_at)
            update_status(db, dataset, source_url, observed_at, True, None)
            db.execute(
                "UPDATE dataset_validators SET observed_at = ? "
                "WHERE dataset = ? AND origin = ? AND observed_at = ?",
                (observed_at.isoformat(), dataset, source_url, old.last_success.isoformat()),
            )
