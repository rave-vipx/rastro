"""Load validated job results through the dataset port."""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any

from rastro.application.consistency import History, history_from_store
from rastro.application.dataset_source import (
    StorageFailed,
    load_source,
    unloaded,
)
from rastro.application.drift import snapshot_result
from rastro.application.run_metrics import record_run_metrics
from rastro.domain.consistency import ConsistencyRule
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.drift import StructuralSnapshot
from rastro.domain.health import OpenDrift
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.job_load import JobLoadResult, LoadedSource, StaleSource
from rastro.domain.job_results import JobRunResult, JobSummary
from rastro.domain.jobs import Job
from rastro.domain.models import ExtractionResult, Issue, Outcome
from rastro.ports.dataset import DatasetStore
from rastro.ports.drift import DriftMonitor, OpenDriftReader

SourceHook = Callable[[int, ExtractionResult[Any], LoadedSource], None]


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
    required: Sequence[str] = (),
    on_source: SourceHook | None = None,
) -> JobLoadResult:
    """Only observed approved fields enter snapshots; failed sources retain their old origins.

    Stale sources and open drift cover only this job's sources. A dataset storage failure
    stops further writes: committed sources keep their counts, the rest report
    ``storage_error`` and the result's ``status`` is ``storage_error`` (exit code 5).
    One ``run_metrics`` row per source is recorded when the store supports it;
    ``required`` names the job's required fields for the coverage count.
    ``on_source(index, result, loaded)`` runs after each source whose load, and drift
    assessment when ``monitor`` is set, completed without a storage failure.
    """
    return load_stream(
        spec,
        names,
        report.results,
        store,
        summary=lambda: report.job,
        rules=rules,
        monitor=monitor,
        snapshots=snapshots,
        drift_state=drift_state,
        required=required,
        on_source=on_source,
    )


def load_stream(
    spec: DatasetSpec,
    names: list[str],
    results: Iterable[ExtractionResult[Any]],
    store: DatasetStore,
    *,
    summary: Callable[[], JobSummary],
    current: Collection[str] | None = None,
    rules: Sequence[ConsistencyRule] = (),
    monitor: DriftMonitor | None = None,
    snapshots: Mapping[str, StructuralSnapshot] | None = None,
    drift_state: OpenDriftReader | None = None,
    required: Sequence[str] = (),
    on_source: SourceHook | None = None,
    job: Job | None = None,
) -> JobLoadResult:
    """`load_results` for results produced lazily: each is loaded before the next is pulled.

    ``summary`` is called once ``results`` is exhausted; ``current`` names the origins of the
    whole job (default: those of the results seen) for stale sources and open drift.
    ``job`` gives the run metrics of a reached cap its configured value.
    """
    sources: list[LoadedSource] = []
    assessments = []
    approved = spec.approved_fields(names)
    history: History = {}
    failed = False
    seen: set[str] = set()
    observed: list[ExtractionResult[Any]] = []
    try:
        history = history_from_store(store, spec.name, approved) if rules else {}
    except DatasetStorageError:
        failed = True  # An unreadable dataset loads nothing, but still reports (exit 5).
    for index, result in enumerate(results):
        seen.add(result.source_url)
        observed.append(result)
        if failed:
            sources.append(unloaded(result))
            continue
        try:
            source = load_source(spec, approved, result, store, rules, history)
        except StorageFailed:
            failed = True
            sources.append(unloaded(result))
            continue
        sources.append(source)
        if monitor is not None and result.status != Outcome.UNCHANGED:
            try:
                assessments.append(
                    monitor.assess(spec.name, _snapshot(result, source, approved, snapshots))
                )
            except DatasetStorageError:
                failed = True
                sources[-1] = source.model_copy(
                    update={"issues": (*source.issues, Issue(code="drift_storage_error"))}
                )
        if not failed and on_source is not None:
            on_source(index, result, sources[-1])
    # Redirected sources are stored under their final URL, which only the results know.
    current = seen if current is None else {*current, *seen}
    refused = [source.inconsistent for source in sources]
    failed = failed or record_run_metrics(
        store, spec.name, observed, required, inconsistent=refused, job=job
    )
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
        job=summary(),
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
