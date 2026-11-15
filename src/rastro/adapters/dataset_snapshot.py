"""Shared pure snapshot identity, provenance and uncertain-match semantics."""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from rastro.adapters.structured import load_json
from rastro.domain.dataset import (
    ChangeKind,
    DatasetRecordKey,
    DatasetStorageError,
    ObservedRecord,
    RecordOrigin,
    SnapshotReport,
    StoredRecord,
    UncertainMatch,
    near_match,
)
from rastro.domain.memory import utc_timestamp
from rastro.domain.sources import Limits, Source


class DatasetSnapshot:
    _limits: Limits
    _match_fields: tuple[str, str, str] | None
    _tolerance: timedelta

    @staticmethod
    def _validate(dataset: str, source: str, at: datetime) -> None:
        DatasetRecordKey(dataset=dataset, identity=("validation",))
        utc_timestamp(at)
        if Source(url=source).evidence_url != source:
            raise DatasetStorageError("Dataset sources require canonical evidence URLs")

    def _classify(
        self,
        rows: dict[str, StoredRecord],
        incoming: dict[str, ObservedRecord],
        source: str,
        *,
        complete: bool,
    ) -> SnapshotReport:
        changes: dict[ChangeKind, list[DatasetRecordKey]] = {kind: [] for kind in ChangeKind}
        for key, row in rows.items():
            if source in row.origins and key not in incoming:
                changes[ChangeKind.MISSING if complete else ChangeKind.UNCERTAIN].append(row.key)
        for key, record in incoming.items():
            old = rows.get(key)
            observed = self._observe(old, record, complete=complete)
            kind = (
                ChangeKind.CREATED
                if old is None
                else ChangeKind.UNCHANGED
                if old.origins.get(source, old).fields == observed.origins[source].fields
                else ChangeKind.UPDATED
            )
            changes[kind].append(record.key)
            rows[key] = observed
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
    def _observe(
        old: StoredRecord | None, record: ObservedRecord, *, complete: bool
    ) -> StoredRecord:
        origins = dict(old.origins) if old else {}
        prior = origins.get(record.source_url)
        values = record.model_dump()
        if prior is not None and not complete:
            values["fields"] = {**prior.fields, **record.fields}
            values["provenance"] = {**prior.provenance, **record.provenance}
        origin = RecordOrigin(
            **values,
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
                    if self._match_fields is not None and near_match(
                        record, origin, self._match_fields, self._tolerance
                    ):
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
