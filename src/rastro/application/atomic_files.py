"""Same-directory atomic file replacement that never follows symlinks (SEC-008)."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from rastro.domain.models import ConfigurationError


class FileChangedError(ConfigurationError):
    """The file no longer holds the bytes the caller based its edit on."""


def _digest_now(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def write_atomic(path: Path, data: bytes, *, expected_digest: str | None = None) -> None:
    """Replace ``path`` through a same-directory temp file; never follows symlinks.

    With ``expected_digest`` (sha256 hex), the bytes being replaced are read right before
    the replace and must match it, or ``FileChangedError`` is raised and nothing changes.
    """
    if path.is_symlink():
        raise ConfigurationError("Symbolic links are not followed")
    mode = path.stat().st_mode & 0o777
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temp, flags, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_digest is not None and _digest_now(path) != expected_digest:
            raise FileChangedError("The file changed since it was read; reload it and retry")
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def fsync_directory(directory: Path) -> None:
    """Make a rename durable by syncing its directory; a no-op where unsupported (Windows)."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
