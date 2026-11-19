"""The checkpoint of an unbounded crawl: versioned JSON in the job's state folder (ADR 0040).

A checkpoint holds the links still to fetch and a digest of every page identity already
seen, so the next run resumes without refetching. It is plain JSON validated on load, never
pickle: a tampered, truncated or unknown-version file is discarded, never executed or
trusted. Files are written atomically with mode 0600 inside a 0700 folder, and symbolic links
are never followed. Seen pages are stored as digests, so the file names no visited query.
Frontier links keep their whole URL, because they must be fetched again.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import structlog
from pydantic import Field, ValidationError

from rastro.application.atomic_files import write_atomic
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.domain.provenance_url import page_identity
from rastro.domain.sources import Contract
from rastro.domain.spider import SpiderPolicy

__all__ = [
    "CheckpointLoad",
    "CheckpointStore",
    "CrawlCheckpoint",
    "FrontierEntry",
    "checkpoint_store",
    "crawl_key",
    "crawl_state",
    "seen_key",
]

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_FOLDER = (".rastro", "crawl")
_MAX_BYTES = 64 * 1024 * 1024
_MAX_ENTRIES = 1_000_000
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
type SeenKey = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class FrontierEntry(Contract):
    """One link still to fetch, at the depth it was discovered."""

    depth: int = Field(ge=0, le=5)
    url: str = Field(min_length=8, max_length=8192, pattern=r"^https?://")


class CrawlCheckpoint(Contract):
    """Version 1 of the checkpoint file."""

    version: Literal[1]
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    frontier: tuple[FrontierEntry, ...] = Field(max_length=_MAX_ENTRIES)
    seen: tuple[SeenKey, ...] = Field(max_length=_MAX_ENTRIES)


@dataclass(frozen=True)
class CheckpointLoad:
    """A usable checkpoint, or none; ``discarded`` when an unusable file was removed."""

    checkpoint: CrawlCheckpoint | None = None
    discarded: bool = False


@dataclass(frozen=True)
class _State:
    folder: Path
    job: str


_STATE: ContextVar[_State | None] = ContextVar("crawl_state", default=None)


def seen_key(url: str) -> str:
    """A digest of a page identity: stored instead of the URL and its query."""
    return hashlib.sha256(page_identity(url).encode()).hexdigest()[:32]


def crawl_key(job: Job) -> str:
    """What makes two runs the same crawl: the fields and the spider table, budget aside."""
    spider = job.spider.model_dump_json(exclude={"time_budget_seconds"}) if job.spider else ""
    fields = ",".join(item.model_dump_json() for item in job.fields)
    return hashlib.sha256(f"{fields}\n{spider}".encode()).hexdigest()


@contextmanager
def crawl_state(folder: Path | None, job: str) -> Iterator[None]:
    """Keep checkpoints of unbounded crawls run inside this block under ``folder``.

    ``job`` names the crawl (see :func:`crawl_key`). Without a folder, nothing is saved and
    an unbounded crawl starts over each run.
    """
    token = _STATE.set(_State(folder, job) if folder is not None else None)
    try:
        yield
    finally:
        _STATE.reset(token)


def checkpoint_store(start_url: str, spider: SpiderPolicy) -> CheckpointStore | None:
    """The store for an unbounded crawl from ``start_url``, or ``None`` when none applies."""
    state = _STATE.get()
    if state is None or not spider.unbounded:
        return None
    key = hashlib.sha256(f"{state.job}\n{page_identity(start_url)}".encode()).hexdigest()
    return CheckpointStore(state.folder.joinpath(*_FOLDER, f"{key[:32]}.json"), key)


@dataclass(frozen=True)
class CheckpointStore:
    """Load, save and clear one crawl's checkpoint file."""

    path: Path
    key: str

    def load(self) -> CheckpointLoad:
        """Read the checkpoint; discard (remove) any file that is not a valid one for us."""
        if not self.path.exists() and not self.path.is_symlink():
            return CheckpointLoad()
        try:
            checkpoint = CrawlCheckpoint.model_validate_json(self._read())
            if checkpoint.key != self.key:
                raise ValueError("checkpoint belongs to another crawl")
        except (OSError, ValueError, ConfigurationError) as error:
            reason = type(error).__name__ if not isinstance(error, ValidationError) else "invalid"
            _LOG.warning("crawl_checkpoint_discarded", reason=reason)
            self._remove()
            return CheckpointLoad(discarded=True)
        return CheckpointLoad(checkpoint)

    def save(self, frontier: Iterable[tuple[int, str]], seen: Iterable[str]) -> bool:
        """Write the checkpoint atomically; ``False`` (and no file) when it cannot be kept."""
        try:
            checkpoint = CrawlCheckpoint(
                version=1,
                key=self.key,
                frontier=tuple(FrontierEntry(depth=d, url=u) for d, u in frontier),
                seen=tuple(seen),
            )
            data = checkpoint.model_dump_json().encode()
            if len(data) > _MAX_BYTES:
                raise ValueError("checkpoint too large")
            self._prepare()
            write_atomic(self.path, data)
        except (OSError, ValueError, ConfigurationError) as error:
            _LOG.warning("crawl_checkpoint_not_saved", reason=type(error).__name__)
            self._remove()
            return False
        return True

    def clear(self) -> bool:
        """Remove the checkpoint; ``True`` when there was one."""
        existed = self.path.exists() or self.path.is_symlink()
        self._remove()
        return existed

    def _read(self) -> bytes:
        self._check_folder()
        fd = os.open(self.path, os.O_RDONLY | _NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError("checkpoint too large")
        return data

    def _prepare(self) -> None:
        """Create the private folder and an empty 0600 file for ``write_atomic`` to replace."""
        self._check_folder()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_folder()
        if self.path.is_symlink():
            raise ConfigurationError("Symbolic links are not followed")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | _NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)

    def _check_folder(self) -> None:
        folder = self.path.parent
        for part in (folder, folder.parent):
            if part.is_symlink():
                raise ConfigurationError("Symbolic links are not followed")

    def _remove(self) -> None:
        try:
            if self.path.is_symlink() or self.path.is_file():
                self.path.unlink()
        except OSError:
            _LOG.warning("crawl_checkpoint_not_removed")
