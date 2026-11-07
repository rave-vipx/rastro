"""Load validated job results through the dataset port (DATA-001, SYNC-002/006/008)."""

from collections.abc import Mapping
from typing import Any

from rastro.application.drift import snapshot_result
from rastro.domain.dataset import ChangeKind, DatasetRecordKey, ObservedRecord
from rastro.domain.drift import StructuralSnapshot
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.job_load import JobLoadResult, LoadedSource, StaleSource
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import Outcome
from rastro.ports.dataset import DatasetStore
from rastro.ports.drift import DriftMonitor


def load_results(
    spec: DatasetSpec,
    names: list[str],
    report: JobRunResult[Any],
    store: DatasetStore,
    *,
    monitor: DriftMonitor | None = None,
    snapshots: Mapping[str, StructuralSnapshot] | None = None,
) -> JobLoadResult:
    """Only observed approved fields enter snapshots; failed sources retain their old origins."""
    sources = []
    assessments = []
    approved = spec.approved_fields(names)
    for result in report.results:
        counts = dict.fromkeys(ChangeKind, 0)
        if result.status in (Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS):
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
            counts = store.apply_snapshot(
                spec.name,
                result.source_url,
                observations,
                complete=result.status == Outcome.SUCCESS,
                observed_at=result.completed_at,
            ).counts
        else:
            store.mark_source_unavailable(
                spec.name, result.source_url, result.completed_at, result.status.value
            )
        if monitor is not None:
            snapshot = snapshot_result(
                result, approved, captured=(snapshots or {}).get(result.job_id)
            )
            assessments.append(monitor.assess(spec.name, snapshot))
        sources.append(
            LoadedSource(source_url=result.source_url, status=result.status, counts=counts)
        )
    return JobLoadResult(
        dataset=spec.name,
        job=report.job,
        sources=tuple(sources),
        stale_sources=stale_sources(store, spec.name),
        drift=tuple(assessments) if monitor is not None else None,
    )


def stale_sources(store: DatasetStore, dataset: str) -> tuple[StaleSource, ...]:
    """Name the stale origins and the retained record keys affected by them."""
    records = store.records(dataset)
    return tuple(
        StaleSource(
            **status.model_dump(),
            affected_keys=tuple(r.key for r in records if status.source_url in r.origins),
        )
        for status in store.source_status(dataset)
        if status.stale
    )
