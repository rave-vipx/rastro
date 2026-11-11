"""Root-confined job file access for Studio: listing, reading and atomic writes."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from rastro.domain.jobs import MAX_JOB_BYTES
from rastro.domain.models import ConfigurationError

SUFFIXES = {".toml", ".json"}
MAX_FILES = 500
MAX_DEPTH = 4


def _safe_relative(raw: str) -> Path:
    if not raw or len(raw) > 1024 or "\x00" in raw or "\\" in raw:
        raise ConfigurationError("Invalid job file path")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in SUFFIXES:
        raise ConfigurationError("Job files must be relative .toml or .json paths inside the root")
    return rel


def resolve_in_root(root: Path, raw: str) -> Path:
    """Return the job file path under ``root``; refuse escapes and symlinks."""
    rel = _safe_relative(raw)
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ConfigurationError("Symbolic links are not followed")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("Job file does not exist inside the root")
    return current


def list_job_files(root: Path) -> list[str]:
    """Relative .toml/.json files below ``root`` (bounded, sorted, symlinks skipped)."""
    found: list[str] = []
    for directory, subdirs, names in os.walk(root, followlinks=False):
        depth = len(Path(directory).relative_to(root).parts)
        subdirs[:] = sorted(
            d for d in subdirs if depth < MAX_DEPTH and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.lower() in SUFFIXES and not path.is_symlink():
                found.append(path.relative_to(root).as_posix())
    return sorted(found)[:MAX_FILES]


def read_job_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_JOB_BYTES + 1)
    if len(raw) > MAX_JOB_BYTES:
        raise ConfigurationError("Job file exceeds 256 KiB")
    return raw


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
    finally:
        temp.unlink(missing_ok=True)
