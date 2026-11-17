"""Studio's content-free run log: what a load reported that the dataset does not keep (N5).

The dataset keeps source status, run metrics and drift. A load result also names issue codes
(for example ``evidence_retention_failed``) and a promotion can be refused; Studio keeps the
last ``MAX_RUNS`` of these per job under ``<root>/.rastro-studio/runs/`` so the Sources and
Alerts views can show them after a restart. Entries hold URLs, counts, Rastro issue codes and
declared field names only, never page content or record values. A missing, oversized or
invalid log reads as empty.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, ValidationError

from rastro.application.atomic_files import fsync_directory
from rastro.domain.issue_codes import KNOWN_CODES
from rastro.domain.job_load import JobLoadResult
from rastro.domain.models import ConfigurationError
from rastro.domain.sources import Contract

CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
LOG_DIR = ".rastro-studio"
MAX_RUNS = 20
MAX_SOURCES = 200
MAX_ISSUES = 10
MAX_LOG_BYTES = 512 * 1024

type Code = Annotated[str, Field(pattern=CODE_PATTERN)]


class LoggedIssue(Contract):
    code: Code
    field: str | None = Field(default=None, max_length=128)


class LoggedSource(Contract):
    url: str = Field(max_length=8192)
    read: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    cap: int | None = Field(default=None, ge=1)
    issues: tuple[LoggedIssue, ...] = Field(default=(), max_length=MAX_ISSUES)


class LoggedRun(Contract):
    at: AwareDatetime
    kind: Literal["load", "promote_refused"] = "load"
    sources: tuple[LoggedSource, ...] = Field(default=(), max_length=MAX_SOURCES)


class RunLog(Contract):
    schema_version: Literal[1] = 1
    runs: tuple[LoggedRun, ...] = Field(default=(), max_length=MAX_RUNS)


def _file(root: Path, job: Path) -> Path:
    name = hashlib.sha256(job.relative_to(root).as_posix().encode()).hexdigest()[:32]
    return root / LOG_DIR / "runs" / f"{name}.json"


def read_runs(root: Path, job: Path) -> tuple[LoggedRun, ...]:
    """Logged runs of ``job``, oldest first; ``()`` when absent or unreadable."""
    path = _file(root, job)
    if any(p.is_symlink() for p in (path, path.parent, path.parent.parent)) or not path.is_file():
        return ()
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            return ()
        return RunLog.model_validate_json(raw).runs
    except (OSError, ValidationError, ValueError):
        return ()


def append_run(root: Path, job: Path, run: LoggedRun) -> None:
    """Add ``run`` and keep the newest ``MAX_RUNS`` that fit in ``MAX_LOG_BYTES``.

    Written atomically, links refused; a run too large on its own is not logged.
    """
    path = _file(root, job)
    for directory in (path.parent.parent, path.parent):
        if directory.is_symlink():
            raise ConfigurationError("Symbolic links are not followed")
        directory.mkdir(mode=0o700, exist_ok=True)
    runs = (*read_runs(root, job), run)[-MAX_RUNS:]
    data = RunLog(runs=runs).model_dump_json().encode()
    while len(data) > MAX_LOG_BYTES and len(runs) > 1:  # the reader drops a larger file
        runs = runs[1:]
        data = RunLog(runs=runs).model_dump_json().encode()
    if len(data) > MAX_LOG_BYTES:
        return  # this run alone is too large: keep the readable log as it was
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(temp, flags, 0o600), "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def remember(root: Path, job: Path, run: LoggedRun) -> None:
    """``append_run`` that never fails the request: the log is a convenience, not the record."""
    try:
        append_run(root, job, run)
    except (OSError, ConfigurationError):
        return


def _issues(raw: object) -> tuple[LoggedIssue, ...]:
    found = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict) or item.get("code") not in KNOWN_CODES:
            continue  # not a Rastro issue code: never logged
        try:
            found.append(LoggedIssue(code=str(item["code"]), field=item.get("field")))
        except ValidationError:
            continue
    return tuple(dict.fromkeys(found))[:MAX_ISSUES]


def from_load(result: JobLoadResult, at: datetime) -> LoggedRun:
    """The log entry for one ``load_job_file`` result."""
    sources = []
    for item in json.loads(result.model_dump_json())["sources"][:MAX_SOURCES]:
        counts = item.get("counts", {})
        read = sum(int(counts.get(k, 0)) for k in ("created", "updated", "unchanged"))
        sources.append(
            LoggedSource(url=item["source_url"], read=read, issues=_issues(item.get("issues")))
        )
    return LoggedRun(at=at, sources=tuple(sources))


def refused(url: str, at: datetime) -> LoggedRun:
    """A promotion that the gates refused for the source at ``url``."""
    issue = LoggedIssue(code="promotion_refused")
    return LoggedRun(
        at=at, kind="promote_refused", sources=(LoggedSource(url=url, issues=(issue,)),)
    )
