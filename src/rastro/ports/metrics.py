"""Optional per-source run metrics; stores without them simply record nothing."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from rastro.domain.metrics import RunMetric


@runtime_checkable
class RunMetricsRecorder(Protocol):
    def record_run_metrics(self, dataset: str, rows: Sequence[RunMetric]) -> None:
        """Append rows atomically and keep only the newest rows of the dataset."""
        ...

    def run_metrics(self, dataset: str, since: datetime | None = None) -> tuple[RunMetric, ...]:
        """Rows from ``since`` (inclusive) in recording order; empty when there are none."""
        ...
