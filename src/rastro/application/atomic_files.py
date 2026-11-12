"""Same-directory atomic file replacement that never follows symlinks (SEC-008)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from rastro.domain.models import ConfigurationError


def write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` through a same-directory temp file; never follows symlinks."""
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
