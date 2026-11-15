"""Dataset overlap lock (`.lock`) and run recovery (`.run.json`) (SCHED-003, SCHED-004)."""

from __future__ import annotations

import errno
import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from rastro.application.consistency import History, history_from_store
from rastro.application.dataset_load import (
    _load_source,
    _snapshot,
    _StorageFailed,
    _unloaded,
    stale_sources,
)
from rastro.application.errors import RunInProgressError
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.job_load import JobLoadResult, LoadedSource, StaleSource
from rastro.domain.job_results import JobRunResult, RecipeSelection
from rastro.domain.models import ConfigurationError, ExtractionResult, Issue
from rastro.domain.schedule import source_evidence_url

if TYPE_CHECKING:
    from rastro.domain.drift import DriftAssessment, StructuralSnapshot
    from rastro.domain.health import OpenDrift
    from rastro.domain.jobs import Job
    from rastro.ports.dataset import DatasetStore
    from rastro.ports.drift import DriftMonitor, OpenDriftReader

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_NOFOLLOW, _PRIVATE = getattr(os, "O_NOFOLLOW", 0), 0o600


def lock_path(dataset_path: Path) -> Path:
    return dataset_path.with_name(f"{dataset_path.name}.lock")


def checkpoint_path(dataset_path: Path) -> Path:
    return dataset_path.with_name(f"{dataset_path.name}.run.json")


def _check_symlinks(dataset_path: Path, sidecar: Path) -> None:
    if sidecar.is_symlink() or any(p.is_symlink() for p in dataset_path.parents):
        raise ConfigurationError("Dataset paths must not contain symlinks", location="dataset.path")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return pid > 0
    except OSError as exc:
        return pid > 0 and exc.errno != errno.ESRCH


def _stale_reason(
    lpath: Path, now: datetime, stale_after: timedelta, is_alive: Callable[[int], bool]
) -> str | None:
    try:
        data = json.loads(lpath.read_text(encoding="utf-8"))
        pid, raw_t = data["pid"], data.get("started_at", data.get("start_time"))
        t = (
            datetime.fromtimestamp(float(raw_t), tz=UTC)
            if isinstance(raw_t, (int, float))
            else datetime.fromisoformat(str(raw_t))
        )
    except (OSError, ValueError, KeyError, TypeError):
        return "invalid_lock"
    if not isinstance(pid, int) or isinstance(pid, bool):
        return "invalid_lock"
    started = t if t.tzinfo is not None else t.replace(tzinfo=UTC)
    if not is_alive(pid):
        return "dead_pid"
    return "expired_lock" if now - started > stale_after else None


def _write_exclusive(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, _PRIVATE)
    try:
        if os.name != "nt":
            os.fchmod(fd, _PRIVATE)
        os.write(fd, payload)
    finally:
        os.close(fd)


@contextmanager
def dataset_lock(
    dataset_path: Path,
    *,
    timeout_seconds: float = 60.0,
    source_count: int = 1,
    held: bool = False,
    clock: Callable[[], datetime] | None = None,
    is_pid_alive: Callable[[int], bool] | None = None,
) -> Iterator[Path]:
    """Acquire `<dataset>.lock` (`O_CREAT|O_EXCL`, `0600`) or raise `RunInProgressError`."""
    lpath = lock_path(dataset_path)
    if held:
        yield lpath
        return
    _check_symlinks(dataset_path, lpath)
    now = (clock or (lambda: datetime.now(UTC)))()
    stale_after = timedelta(seconds=float(timeout_seconds) * max(source_count, 1) + 3_600.0)
    raw = json.dumps({"pid": os.getpid(), "started_at": now.isoformat()}).encode()
    try:
        _write_exclusive(lpath, raw)
    except FileExistsError:
        reason = _stale_reason(lpath, now, stale_after, is_pid_alive or _pid_alive)
        if reason is not None:
            _LOG.warning("stale_lock_replaced", reason=reason)
            lpath.unlink(missing_ok=True)
            try:
                _write_exclusive(lpath, raw)
            except FileExistsError:
                reason = None
        if reason is None:
            raise RunInProgressError(
                "Another load is already in progress for this dataset", location="dataset.path"
            ) from None
    try:
        yield lpath
    finally:
        lpath.unlink(missing_ok=True)


def write_checkpoint(
    dataset_path: Path, run_id: str, completed_sources: Sequence[str], *, dataset: str | None = None
) -> Path:
    """Atomically write `<dataset>.run.json` with owner-only `0600` permissions."""
    cpath = checkpoint_path(dataset_path)
    _check_symlinks(dataset_path, cpath)
    tmp = dataset_path.with_name(f".{dataset_path.name}.run.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    urls = list(dict.fromkeys(completed_sources))
    body = {"schema_version": 1, "run_id": run_id, "dataset": dataset, "completed_sources": urls}
    try:
        _write_exclusive(tmp, json.dumps(body, ensure_ascii=True).encode())
        os.replace(tmp, cpath)
        return cpath
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_checkpoint(
    dataset_path: Path, *, dataset: str | None = None
) -> tuple[str, tuple[str, ...]] | None:
    """Return `(run_id, completed_sources)` from `<dataset>.run.json`, or `None` if absent."""
    cpath = checkpoint_path(dataset_path)
    _check_symlinks(dataset_path, cpath)
    try:
        data = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or (dataset and data.get("dataset") not in (None, dataset)):
        return None
    if not isinstance(urls := data.get("completed_sources", data.get("completed", ())), list):
        return None
    return str(data.get("run_id") or ""), tuple(str(u) for u in urls if isinstance(u, str))


def clear_checkpoint(dataset_path: Path) -> None:
    checkpoint_path(dataset_path).unlink(missing_ok=True)


def run_checkpointed_load(
    raw_path: Path,
    job: Job,
    store: DatasetStore,
    *,
    extract_fn: Callable[[Job], JobRunResult[Any]],
    monitor: DriftMonitor | None = None,
    snapshots: Mapping[str, StructuralSnapshot] | None = None,
    drift_state: OpenDriftReader | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[JobRunResult[Any], JobLoadResult]:
    """Run and load `job` source-by-source, updating `.run.json` and clearing it when complete."""
    assert job.dataset is not None
    spec, name, approved = job.dataset, job.dataset.name, tuple(f.name for f in job.fields)
    existing = read_checkpoint(raw_path, dataset=name)
    done = list(existing[1]) if existing else []
    rem = tuple(s for s in job.sources if source_evidence_url(job, s) not in set(done))
    run_id = (existing[0] if existing and rem else "") or uuid4().hex[:16]
    completed, active = (
        (done, job.model_copy(update={"sources": rem})) if existing and rem else ([], job)
    )
    write_checkpoint(raw_path, run_id, completed, dataset=name)
    history: History = {}
    storage_failed = stopped_early = False
    try:
        history = history_from_store(store, name, approved) if job.consistency else {}
    except DatasetStorageError:
        storage_failed = True
    results: list[ExtractionResult[Any]] = []
    recipes: list[RecipeSelection] = []
    loaded_sources: list[LoadedSource] = []
    drift_items: list[DriftAssessment] = []
    for index, source in enumerate(active.sources, start=1):
        sub_report = extract_fn(active.model_copy(update={"sources": (source,)}))
        results.append(res := sub_report.results[0])
        recipes.extend(r.model_copy(update={"source_index": index}) for r in sub_report.recipes)
        if storage_failed:
            loaded_sources.append(_unloaded(res))
            continue
        try:
            src_loaded = _load_source(spec, approved, res, store, job.consistency, history)
        except _StorageFailed:
            storage_failed = True
            loaded_sources.append(_unloaded(res))
            continue
        if monitor is not None:
            try:
                drift_items.append(
                    monitor.assess(name, _snapshot(res, src_loaded, approved, snapshots))
                )
            except DatasetStorageError:
                storage_failed = True
                src_loaded = src_loaded.model_copy(
                    update={"issues": (*src_loaded.issues, Issue(code="drift_storage_error"))}
                )
        loaded_sources.append(src_loaded)
        if not storage_failed:
            completed.append(source_evidence_url(job, source))
            write_checkpoint(raw_path, run_id, completed, dataset=name)
        if should_stop is not None and should_stop():
            stopped_early = index < len(active.sources)
            break
    report = JobRunResult.from_results(
        schema_fingerprint=sub_report.job.schema_fingerprint, results=results
    ).model_copy(update={"recipes": tuple(recipes)})
    current = {source_evidence_url(job, s) for s in job.sources}
    stale: tuple[StaleSource, ...] = ()
    open_drift: tuple[OpenDrift, ...] = ()
    try:
        stale = stale_sources(store, name, current)
        if drift_state is not None:
            open_drift = tuple(d for d in drift_state.open_drift(name) if d.source_url in current)
    except DatasetStorageError:
        storage_failed = True
    drift = tuple(drift_items) if monitor is not None else None
    loaded = JobLoadResult(
        dataset=name,
        job=report.job,
        sources=tuple(loaded_sources),
        stale_sources=stale,
        drift=drift,
        open_drift=open_drift,
    )
    if storage_failed:
        return report, loaded.model_copy(update={"status": "storage_error"})
    if not stopped_early:
        clear_checkpoint(raw_path)
    return report, loaded
