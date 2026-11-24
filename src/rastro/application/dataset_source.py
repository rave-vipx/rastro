"""Load one source's result into a dataset store: observations, failures and storage errors."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from rastro.application.consistency import History, evaluate_consistency
from rastro.application.pagination_records import is_complete_snapshot
from rastro.domain.consistency import ConsistencyRule
from rastro.domain.dataset import ChangeKind, DatasetRecordKey, DatasetStorageError, ObservedRecord
from rastro.domain.failure_reason import failure_detail
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.job_load import LoadedSource
from rastro.domain.models import ExtractionResult, Issue, Outcome
from rastro.ports.dataset import DatasetStore, DatasetValidators, SourceFailureRecorder

SURFACED = frozenset({"ladder_exhausted"})
"""Run issues a loaded source repeats, so ``job load`` output says why the site stayed shut."""


def unloaded(result: ExtractionResult[Any]) -> LoadedSource:
    """Nothing of this source was written because dataset storage failed."""
    return LoadedSource(
        source_url=result.source_url,
        status=result.status,
        counts=dict.fromkeys(ChangeKind, 0),
        issues=(Issue(code="storage_error"),),
    )


def load_source(
    spec: DatasetSpec,
    approved: tuple[str, ...],
    result: ExtractionResult[Any],
    store: DatasetStore,
    rules: Sequence[ConsistencyRule],
    history: History,
) -> LoadedSource:
    counts = dict.fromkeys(ChangeKind, 0)
    consistency = None
    reason = None
    dropped: tuple[Issue, ...] = ()
    next_action: Literal["rastro recipe repair"] | None = None
    if result.status == Outcome.UNCHANGED:
        with storage():
            if not isinstance(store, DatasetValidators):
                raise DatasetStorageError("Dataset does not support conditional requests")
            store.mark_source_unchanged(spec.name, result.source_url, result.completed_at)
            counts[ChangeKind.UNCHANGED] = sum(
                result.source_url in row.origins for row in store.records(spec.name)
            )
    elif result.status in (Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS):
        observations, dropped = build_observations(spec, approved, result)
        if rules:
            consistency = evaluate_consistency(
                rules,
                [o.fields for o in observations],
                result.completed_at,
                declared_fields=approved,
                history=history,
                rejected=result.metrics.rejected,
            )
        with storage():
            if consistency is not None and consistency.verdict == "inconsistent":
                store.mark_source_unavailable(
                    spec.name, result.source_url, result.completed_at, "consistency_failed"
                )
                next_action = "rastro recipe repair"
            else:
                counts = store.apply_snapshot(
                    spec.name,
                    result.source_url,
                    observations,
                    complete=is_complete_snapshot(result) and not dropped,
                    observed_at=result.completed_at,
                ).counts
    else:
        reason = failure_detail(result)
        with storage():
            record_failure(store, spec.name, result, reason)
        if result.status == Outcome.MISSING_DATA:
            next_action = "rastro recipe repair"
    return LoadedSource(
        source_url=result.source_url,
        status=result.status,
        counts=counts,
        consistency=consistency,
        next_action=next_action,
        reason=reason,
        issues=(*dropped, *(i for i in result.issues if i.code in SURFACED)),
    )


class StorageFailed(Exception):
    """A dataset write failed; earlier sources stay committed and are still reported."""


@contextmanager
def storage() -> Iterator[None]:
    try:
        yield
    except DatasetStorageError as error:
        raise StorageFailed from error


def record_failure(
    store: DatasetStore, dataset: str, result: ExtractionResult[Any], detail: str | None
) -> None:
    if isinstance(store, SourceFailureRecorder):
        store.record_source_failure(
            dataset, result.source_url, result.completed_at, result.status.value, detail
        )
    else:
        store.mark_source_unavailable(
            dataset, result.source_url, result.completed_at, result.status.value
        )


def build_observations(
    spec: DatasetSpec, approved: tuple[str, ...], result: ExtractionResult[Any]
) -> tuple[list[ObservedRecord], tuple[Issue, ...]]:
    """One observation per record; a record that cannot be stored is dropped on its own."""
    observations = []
    dropped = []
    for index, record in enumerate(result.records):
        try:
            values = record.data.model_dump(mode="json")
            fields = {name: values[name] for name in approved if name in record.evidence}
            observations.append(
                ObservedRecord(
                    key=DatasetRecordKey.from_fields(spec.name, fields, spec.identity),
                    source_url=result.source_url,
                    observed_at=result.completed_at,
                    fields=fields,
                    provenance={name: record.evidence[name] for name in fields},
                )
            )
        except (ValidationError, PydanticSerializationError):
            dropped.append(Issue(code="invalid_record", record_index=index))
    return observations, tuple(dropped)
