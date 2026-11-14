"""Load validated job results through the dataset port (DATA-001, SYNC-002/006/008)."""

from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal

from rastro.application.consistency import History, evaluate_consistency, history_from_store
from rastro.application.drift import snapshot_result
from rastro.domain.consistency import ConsistencyRule
from rastro.domain.dataset import ChangeKind, DatasetRecordKey, DatasetStorageError, ObservedRecord
from rastro.domain.drift import StructuralSnapshot
from rastro.domain.failure_reason import failure_detail
from rastro.domain.health import OpenDrift
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.job_load import JobLoadResult, LoadedSource, StaleSource
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ExtractionResult, Issue, Outcome
from rastro.ports.dataset import DatasetStore, SourceFailureRecorder
from rastro.ports.drift import DriftMonitor, OpenDriftReader


def load_results(
    spec: DatasetSpec,
    names: list[str],
    report: JobRunResult[Any],
    store: DatasetStore,
    *,
    rules: Sequence[ConsistencyRule] = (),
    monitor: DriftMonitor | None = None,
    snapshots: Mapping[str, StructuralSnapshot] | None = None,
    drift_state: OpenDriftReader | None = None,
) -> JobLoadResult:
    """Only observed approved fields enter snapshots; failed sources retain their old origins.

    Stale sources and open drift cover only this job's sources. A dataset storage failure
    stops further writes: committed sources keep their counts, the rest report
    ``storage_error`` and the result's ``status`` is ``storage_error`` (exit code 5).
    """
    sources: list[LoadedSource] = []
    assessments = []
    approved = spec.approved_fields(names)
    history: History = {}
    failed = False
    try:
        history = history_from_store(store, spec.name, approved) if rules else {}
    except DatasetStorageError:
        failed = True  # An unreadable dataset loads nothing, but still reports (exit 5).
    for result in report.results:
        if failed:
            sources.append(_unloaded(result))
            continue
        try:
            source = _load_source(spec, approved, result, store, rules, history)
        except _StorageFailed:
            failed = True
            sources.append(_unloaded(result))
            continue
        sources.append(source)
        if monitor is not None:
            try:
                assessments.append(
                    monitor.assess(spec.name, _snapshot(result, source, approved, snapshots))
                )
            except DatasetStorageError:
                failed = True
                sources[-1] = source.model_copy(
                    update={"issues": (*source.issues, Issue(code="drift_storage_error"))}
                )
    current = {result.source_url for result in report.results}
    stale: tuple[StaleSource, ...] = ()
    open_drift: tuple[OpenDrift, ...] = ()
    try:
        stale = stale_sources(store, spec.name, current)
        if drift_state is not None:
            open_drift = tuple(
                d for d in drift_state.open_drift(spec.name) if d.source_url in current
            )
    except DatasetStorageError:
        failed = True
    loaded = JobLoadResult(
        dataset=spec.name,
        job=report.job,
        sources=tuple(sources),
        stale_sources=stale,
        drift=tuple(assessments) if monitor is not None else None,
        open_drift=open_drift,
    )
    return loaded.model_copy(update={"status": "storage_error"}) if failed else loaded


def _snapshot(
    result: ExtractionResult[Any],
    source: LoadedSource,
    approved: tuple[str, ...],
    snapshots: Mapping[str, StructuralSnapshot] | None,
) -> StructuralSnapshot:
    if source.inconsistent:
        result = result.model_copy(update={"status": Outcome.INSUFFICIENT_EVIDENCE, "records": ()})
    return snapshot_result(result, approved, captured=(snapshots or {}).get(result.job_id))


def _unloaded(result: ExtractionResult[Any]) -> LoadedSource:
    """Nothing of this source was written because dataset storage failed."""
    return LoadedSource(
        source_url=result.source_url,
        status=result.status,
        counts=dict.fromkeys(ChangeKind, 0),
        issues=(Issue(code="storage_error"),),
    )


def _load_source(
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
    next_action: Literal["rastro recipe repair"] | None = None
    if result.status in (Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS):
        observations = _observations(spec, approved, result)
        if rules:
            consistency = evaluate_consistency(
                rules,
                [o.fields for o in observations],
                result.completed_at,
                declared_fields=approved,
                history=history,
                rejected=result.metrics.rejected,
            )
        with _storage():
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
                    complete=result.status == Outcome.SUCCESS,
                    observed_at=result.completed_at,
                ).counts
    else:
        reason = failure_detail(result)
        with _storage():
            _record_failure(store, spec.name, result, reason)
        if result.status == Outcome.MISSING_DATA:
            next_action = "rastro recipe repair"
    return LoadedSource(
        source_url=result.source_url,
        status=result.status,
        counts=counts,
        consistency=consistency,
        next_action=next_action,
        reason=reason,
    )


class _StorageFailed(Exception):
    """A dataset write failed; earlier sources stay committed and are still reported."""


@contextmanager
def _storage() -> Iterator[None]:
    try:
        yield
    except DatasetStorageError as error:
        raise _StorageFailed from error


def _record_failure(
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


def _observations(
    spec: DatasetSpec, approved: tuple[str, ...], result: ExtractionResult[Any]
) -> list[ObservedRecord]:
    observations = []
    for record in result.records:
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
    return observations


def stale_sources(
    store: DatasetStore, dataset: str, sources: Collection[str] | None = None
) -> tuple[StaleSource, ...]:
    """Name the stale origins and the retained record keys affected by them.

    With ``sources``, only those origins count: a source removed from the job, or whose URL
    changed, no longer makes the dataset stale (``rastro job forget-source`` removes it).
    """
    records = store.records(dataset)
    return tuple(
        StaleSource(
            **status.model_dump(),
            affected_keys=tuple(r.key for r in records if status.source_url in r.origins),
        )
        for status in store.source_status(dataset)
        if status.stale and (sources is None or status.source_url in sources)
    )
