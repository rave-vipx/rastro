"""Persistence seam for approved observations; no acquisition or recipe operations."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, TextIO, runtime_checkable

from rastro.domain.dataset import ObservedRecord, SnapshotReport, SourceStatus, StoredRecord


@runtime_checkable
class DatasetStore(Protocol):
    def apply_snapshot(
        self,
        dataset: str,
        source_url: str,
        records: Sequence[ObservedRecord],
        *,
        complete: bool,
        observed_at: datetime,
    ) -> SnapshotReport: ...

    def mark_source_unavailable(
        self, dataset: str, source_url: str, observed_at: datetime, reason: str
    ) -> None: ...

    def records(self, dataset: str) -> tuple[StoredRecord, ...]: ...

    def export_jsonl(self, dataset: str, stream: TextIO) -> None: ...

    def source_status(self, dataset: str) -> tuple[SourceStatus, ...]: ...
