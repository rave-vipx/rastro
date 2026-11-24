"""A short-lived page cache for Studio iteration, never part of a job run.

Studio re-runs a draft recipe many times while a person edits it. ``CachingAcquisition``
wraps the acquisition Studio already uses and answers a repeat of the same page from a
state folder for ``ttl_seconds``, so tuning selectors does not hit the site again. Only
successful documents are stored. A URL whose query carries a secret-like key is never
stored. Jobs, ``job run`` and ``job load`` do not accept this wrapper: they always fetch.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import Representation, Source
from rastro.domain.provenance_url import canonical_page_url, page_identity
from rastro.domain.results import Outcome
from rastro.ports.acquisition import AcquisitionPort

DEFAULT_TTL_SECONDS = 900.0
MAX_ENTRY_BYTES = 8 * 1024 * 1024


class DevCache:
    """Cached documents keyed by canonical page URL and representation."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def cacheable(self, url: str) -> bool:
        """False when the URL carries a secret-like query key."""
        return canonical_page_url(url) == page_identity(url)

    def _path(self, url: str, representation: Representation) -> Path:
        key = f"{canonical_page_url(url)}\0{representation}".encode()
        return self.directory / f"{hashlib.sha256(key).hexdigest()}.json"

    def get(self, url: str, representation: Representation) -> AcquisitionResult | None:
        if not self.cacheable(url):
            return None
        path = self._path(url, representation)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_ENTRY_BYTES:
                return None
            entry = json.loads(path.read_bytes())
            fresh = self._clock() - float(entry["stored_at"]) < self.ttl_seconds
            body = entry["body"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not fresh or not isinstance(body, str):
            return None
        return AcquisitionResult(
            url=url,
            body=body,
            status=Outcome.SUCCESS,
            issue_code=None,
            http_status=entry.get("http_status"),
        )

    def put(self, url: str, representation: Representation, result: AcquisitionResult) -> None:
        if result.body is None or result.status != Outcome.SUCCESS or not self.cacheable(url):
            return
        entry = {
            "url": canonical_page_url(url),
            "representation": representation,
            "stored_at": self._clock(),
            "http_status": result.http_status,
            "body": result.body,
        }
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.prune()
        path = self._path(url, representation)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            with os.fdopen(os.open(temp, flags, 0o600), "w", encoding="utf-8") as handle:
                json.dump(entry, handle)
            os.replace(temp, path)
        except OSError:
            temp.unlink(missing_ok=True)

    def prune(self) -> int:
        """Delete entries (and leftover temporary files) older than the TTL; return how many.

        Age is the file's modification time, set when the entry was written, so pruning
        never reads a document. Called on every write; symlinks are never followed.
        """
        removed = 0
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return 0
        now = self._clock()
        for path in entries:
            if not path.name.endswith((".json", ".tmp")):
                continue
            try:
                info = path.lstat()
                if now - info.st_mtime >= self.ttl_seconds or path.is_symlink():
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


class CachingAcquisition:
    """An ``AcquisitionPort`` that answers repeat pages from a ``DevCache``."""

    def __init__(self, inner: AcquisitionPort, cache: DevCache) -> None:
        self._inner = inner
        self._cache = cache

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        hit = self._cache.get(source.url, representation)
        if hit is not None and len(hit.body or "") <= max_bytes:
            return hit
        result = await self._inner.acquire(
            source, max_bytes=max_bytes, representation=representation
        )
        self._cache.put(source.url, representation, result)
        return result
