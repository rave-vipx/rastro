"""Optional PostgreSQL datasets; one lazy connection, transactional snapshots (DATA-001)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any, TextIO

from rastro.adapters.dataset_snapshot import DatasetSnapshot
from rastro.adapters.postgres_schema import migrate, parameters, safe
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
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.sources import ConfigurationError, Limits

if TYPE_CHECKING:
    from psycopg import Connection


class PostgresDatasetStore(DatasetSnapshot):
    @safe
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "rastro",
        allow_remote: bool = False,
        near_match_fields: tuple[str, str, str] | None = ("title", "venue", "start_time"),
        near_match_tolerance: timedelta = timedelta(minutes=30),
        limits: Limits | None = None,
    ) -> None:
        self._params = parameters(dsn, schema, allow_remote)
        self._schema, self._limits = schema, limits or Limits()
        self._match_fields, self._tolerance = near_match_fields, near_match_tolerance
        self._db: Connection[Any] | None = None
        if near_match_tolerance < timedelta(0) or (
            near_match_fields is not None and len(near_match_fields) != 3
        ):
            raise DatasetStorageError("Invalid near-match configuration")

    @safe
    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[Connection[Any]]:
        import psycopg

        if self._db is None:
            if os.environ.get("PGSERVICE") or os.environ.get("PGHOSTADDR"):
                raise ConfigurationError(
                    "Use explicit PostgreSQL DSN options instead of PGSERVICE or PGHOSTADDR"
                )
            self._db = psycopg.connect(**self._params, autocommit=True)
        with self._db.transaction():
            self._db.execute("SET LOCAL statement_timeout = '5s'")
            migrate(self._db, self._schema, write=write)
            yield self._db

    def _read(self, db: Connection[Any], dataset: str) -> dict[str, StoredRecord]:
        rows: dict[str, StoredRecord] = {}
        size = 0
        for key, payload in db.execute(
            "SELECT key, payload FROM dataset_records WHERE dataset = %s ORDER BY key LIMIT %s",
            (dataset, self._limits.max_records + 1),
        ):
            size += len(payload.encode())
            if len(rows) >= self._limits.max_records or size > self._limits.max_json_bytes:
                raise DatasetStorageError("Dataset exceeds configured limits")
            rows[key] = StoredRecord.model_validate(load_json(payload, self._limits))
        return rows

    @staticmethod
    def _status(db: Connection[Any], dataset: str, source: str) -> SourceStatus | None:
        row = db.execute(
            "SELECT payload FROM dataset_sources WHERE dataset = %s AND source = %s",
            (dataset, source),
        ).fetchone()
        return SourceStatus.model_validate_json(row[0]) if row else None

    def _update_status(
        self,
        db: Connection[Any],
        dataset: str,
        source: str,
        at: datetime,
        complete: bool,
        reason: str | None,
        detail: str | None = None,
    ) -> None:
        old = self._status(db, dataset, source)
        at = max(at, old.last_attempt) if old else at
        status = SourceStatus(
            source_url=source,
            last_attempt=at,
            stale=not complete,
            last_success=at if complete else old.last_success if old else None,
            reason=reason,
            detail=detail,
        )
        db.execute(
            "INSERT INTO dataset_sources VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (dataset, identity_hash) DO UPDATE SET payload = EXCLUDED.payload",
            (dataset, source, status.model_dump_json(), sha256(source.encode()).hexdigest()),
        )

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
        with self._connection(write=True) as db:
            last = self._status(db, dataset, source_url)
            if last is not None and observed_at < last.last_attempt:
                observed_at = last.last_attempt
                incoming = {
                    key: item.model_copy(update={"observed_at": observed_at})
                    for key, item in incoming.items()
                }
            rows = self._read(db, dataset)
            self._update_status(
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
            with db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO dataset_records VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (dataset, identity_hash) DO UPDATE SET payload = EXCLUDED.payload",
                    [(d, k, p, sha256(k.encode()).hexdigest()) for d, k, p in payloads],
                )
        return report

    @safe
    def record_source_failure(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str, detail: str | None
    ) -> None:
        self._validate(dataset, source_url, observed_at)
        reason = check_reason(reason)
        detail = None if detail is None else check_reason(detail)
        with self._connection(write=True) as db:
            self._update_status(db, dataset, source_url, observed_at, False, reason, detail)

    def mark_source_unavailable(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str
    ) -> None:
        self.record_source_failure(dataset, source_url, observed_at, reason, None)

    @safe
    def records(self, dataset: str) -> tuple[StoredRecord, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with self._connection() as db:
            return tuple(self._read(db, dataset).values())

    @safe
    def source_status(self, dataset: str) -> tuple[SourceStatus, ...]:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        with self._connection() as db:
            return tuple(
                SourceStatus.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM dataset_sources WHERE dataset = %s ORDER BY source",
                    (dataset,),
                )
            )

    @safe
    def export_jsonl(self, dataset: str, stream: TextIO) -> None:
        for record in self.records(dataset):
            stream.write(record.model_dump_json() + "\n")

    @safe
    def forget_source(self, dataset: str, source_url: str) -> int:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        try:
            with self._connection() as db:
                return db.execute(
                    "DELETE FROM dataset_sources WHERE dataset = %s AND source = %s",
                    (dataset, source_url),
                ).rowcount
        except DatasetNotFoundError:
            return 0
