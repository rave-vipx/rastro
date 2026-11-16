"""Run and load a job source by source with a recoverable checkpoint (SCHED-004)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from rastro.application.dataset_load import load_stream
from rastro.application.run_lock import (
    SourceKey,
    clear_checkpoint,
    read_checkpoint,
    source_key,
    write_checkpoint,
)
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobRunResult, RecipeSelection
from rastro.domain.models import ExtractionResult

if TYPE_CHECKING:
    from rastro.domain.drift import StructuralSnapshot
    from rastro.domain.jobs import Job
    from rastro.ports.dataset import DatasetStore
    from rastro.ports.drift import DriftMonitor, OpenDriftReader


def run_checkpointed_load(
    raw_path: Path,
    job: Job,
    store: DatasetStore,
    *,
    extract_fn: Callable[[Job], JobRunResult[Any]],
    positions: Sequence[int] | None = None,
    monitor: DriftMonitor | None = None,
    snapshots: Mapping[str, StructuralSnapshot] | None = None,
    drift_state: OpenDriftReader | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[JobRunResult[Any], JobLoadResult]:
    """Run and load `job` source by source, updating `.run.json` and clearing it when complete.

    ``positions`` are the sources' positions in the job file (default: their order here), so a
    run restricted to some sources checkpoints the same keys as a full run.
    """
    assert job.dataset is not None
    name = job.dataset.name
    slots = [
        (source_key(job, pos, source), source)
        for pos, source in zip(positions or range(len(job.sources)), job.sources, strict=True)
    ]
    existing = read_checkpoint(raw_path, dataset=name)
    pending = [slot for slot in slots if existing is None or slot[0] not in set(existing[1])]
    resume = existing is not None and bool(pending)
    run_id = (existing[0] if existing and resume else "") or uuid4().hex[:16]
    completed: list[SourceKey] = list(existing[1]) if existing and resume else []
    active = pending if resume else slots
    write_checkpoint(raw_path, run_id, completed, dataset=name)
    results: list[ExtractionResult[Any]] = []
    recipes: list[RecipeSelection] = []
    fingerprint, stopped_early = "", False

    def extracted() -> Iterator[ExtractionResult[Any]]:
        nonlocal fingerprint, stopped_early
        for number, (_, source) in enumerate(active, start=1):
            sub_report = extract_fn(job.model_copy(update={"sources": (source,)}))
            fingerprint = sub_report.job.schema_fingerprint
            results.append(result := sub_report.results[0])
            recipes.extend(
                r.model_copy(update={"source_index": number}) for r in sub_report.recipes
            )
            yield result  # the consumer loads it and checkpoints it before we resume here
            if should_stop is not None and should_stop():
                stopped_early = number < len(active)
                return

    def checkpoint(index: int, _result: ExtractionResult[Any], _loaded: Any) -> None:
        completed.append(active[index][0])
        write_checkpoint(raw_path, run_id, completed, dataset=name)

    def report() -> JobRunResult[Any]:
        built = JobRunResult.from_results(schema_fingerprint=fingerprint, results=results)
        return built.model_copy(update={"recipes": tuple(recipes)})

    loaded = load_stream(
        job.dataset,
        [f.name for f in job.fields],
        extracted(),
        store,
        summary=lambda: report().job,
        current={key[1] for key, _ in slots},
        rules=job.consistency,
        monitor=monitor,
        snapshots=snapshots,
        drift_state=drift_state,
        on_source=checkpoint,
    )
    if loaded.status != "storage_error" and not stopped_early:
        clear_checkpoint(raw_path)
    return report(), loaded
