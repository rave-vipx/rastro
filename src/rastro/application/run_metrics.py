"""Record one metrics row per source after a load (OBS-003); SQLite datasets only."""

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from rastro.domain.dataset import DatasetStorageError
from rastro.domain.metrics import metric_from_result
from rastro.domain.models import ExtractionResult
from rastro.ports.metrics import RunMetricsRecorder


def record_run_metrics(
    store: object, dataset: str, results: Sequence[ExtractionResult[Any]], required: Sequence[str]
) -> bool:
    """Whether recording failed; stores without metrics (Postgres) record nothing."""
    if not isinstance(store, RunMetricsRecorder):
        return False
    run_id = uuid4().hex
    try:
        store.record_run_metrics(
            dataset, [metric_from_result(run_id, r, required) for r in results]
        )
    except DatasetStorageError:
        return True
    return False
