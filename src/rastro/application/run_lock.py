"""Dataset overlap lock (`.lock`) and run recovery (`.run.json`) (SCHED-003, SCHED-004)."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from rastro.application.errors import RunInProgressError
from rastro.application.process_alive import pid_alive
from rastro.domain.models import ConfigurationError
from rastro.domain.schedule import source_evidence_url

if TYPE_CHECKING:
    from rastro.domain.job_sources import JobSource
    from rastro.domain.jobs import Job

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_NOFOLLOW, _PRIVATE = getattr(os, "O_NOFOLLOW", 0), 0o600
SourceKey = tuple[int, str]


def lock_path(dataset_path: Path) -> Path:
    return dataset_path.with_name(f"{dataset_path.name}.lock")


def checkpoint_path(dataset_path: Path) -> Path:
    return dataset_path.with_name(f"{dataset_path.name}.run.json")


def source_key(job: Job, position: int, source: JobSource) -> SourceKey:
    """Checkpoint identity of a source: its position in the job file and its evidence URL."""
    return position, source_evidence_url(job, source)


def _check_symlinks(dataset_path: Path, sidecar: Path) -> None:
    if sidecar.is_symlink() or any(p.is_symlink() for p in dataset_path.parents):
        raise ConfigurationError("Dataset paths must not contain symlinks", location="dataset.path")


def _read_lock(lpath: Path) -> bytes | None:
    try:
        return lpath.read_bytes()
    except OSError:
        return None


def _stale_reason(
    raw: bytes, now: datetime, stale_after: timedelta, is_alive: Callable[[int], bool]
) -> str | None:
    try:
        data = json.loads(raw.decode("utf-8"))
        pid, raw_t = data["pid"], data.get("started_at", data.get("start_time"))
        t = (
            datetime.fromtimestamp(float(raw_t), tz=UTC)
            if isinstance(raw_t, (int, float))
            else datetime.fromisoformat(str(raw_t))
        )
    except (ValueError, KeyError, TypeError):
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


def _take_over(lpath: Path, judged: bytes) -> bool:
    """Remove the lock we judged stale, but only if it is still that very lock.

    ``os.replace`` lets exactly one starter move a given file; whoever loses finds another
    lock there. If the file we moved is not the one we judged, it belongs to a live starter:
    put it back without overwriting anything (``os.link`` fails if a lock exists) and yield.
    """
    aside = lpath.with_name(f"{lpath.name}.stale-{uuid4().hex}")
    try:
        os.replace(lpath, aside)
    except FileNotFoundError:
        return True  # released meanwhile; the exclusive create below arbitrates
    except OSError:
        return False
    same = _read_lock(aside) == judged
    if not same:
        with suppress(OSError):
            os.link(aside, lpath)
    aside.unlink(missing_ok=True)
    return same


def _acquire(
    lpath: Path, raw: bytes, now: datetime, stale_after: timedelta, is_alive: Callable[[int], bool]
) -> None:
    for _ in range(2):
        try:
            _write_exclusive(lpath, raw)
            return
        except FileExistsError:
            pass
        if (judged := _read_lock(lpath)) is None:
            continue  # released between our two looks: try the exclusive create again
        reason = _stale_reason(judged, now, stale_after, is_alive)
        if reason is not None and _take_over(lpath, judged):
            _LOG.warning("stale_lock_replaced", reason=reason)
            try:
                _write_exclusive(lpath, raw)
                return
            except FileExistsError:
                pass
        break
    raise RunInProgressError(
        "Another load is already in progress for this dataset", location="dataset.path"
    )


def _release(lpath: Path, pid: int, token: str) -> None:
    """Delete the lock only while it still holds our pid and token (never a successor's)."""
    try:
        data = json.loads(lpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("pid") == pid and data.get("token") == token:
        lpath.unlink(missing_ok=True)


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
    pid, token = os.getpid(), uuid4().hex
    raw = json.dumps({"pid": pid, "token": token, "started_at": now.isoformat()}).encode()
    _acquire(lpath, raw, now, stale_after, is_pid_alive or pid_alive)
    try:
        yield lpath
    finally:
        _release(lpath, pid, token)


def write_checkpoint(
    dataset_path: Path,
    run_id: str,
    completed_sources: Sequence[SourceKey],
    *,
    dataset: str | None = None,
) -> Path:
    """Atomically write `<dataset>.run.json` with owner-only `0600` permissions."""
    cpath = checkpoint_path(dataset_path)
    _check_symlinks(dataset_path, cpath)
    tmp = dataset_path.with_name(f".{dataset_path.name}.run.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    done = [{"index": i, "url": u} for i, u in dict.fromkeys(completed_sources)]
    body = {"schema_version": 1, "run_id": run_id, "dataset": dataset, "completed_sources": done}
    try:
        _write_exclusive(tmp, json.dumps(body, ensure_ascii=True).encode())
        os.replace(tmp, cpath)
        return cpath
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_checkpoint(
    dataset_path: Path, *, dataset: str | None = None
) -> tuple[str, tuple[SourceKey, ...]] | None:
    """Return `(run_id, completed (index, url) pairs)` from `<dataset>.run.json`, or `None`."""
    cpath = checkpoint_path(dataset_path)
    _check_symlinks(dataset_path, cpath)
    try:
        data = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or (dataset and data.get("dataset") not in (None, dataset)):
        return None
    if not isinstance(items := data.get("completed_sources"), list):
        return None
    done = {
        (item["index"], item["url"])
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("index"), int)
        and isinstance(item.get("url"), str)
    }
    return str(data.get("run_id") or ""), tuple(sorted(done))


def clear_checkpoint(dataset_path: Path) -> None:
    checkpoint_path(dataset_path).unlink(missing_ok=True)
