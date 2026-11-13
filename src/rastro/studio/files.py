"""Root-confined file access for Studio: listing, reading, new paths and atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from rastro.application.atomic_files import write_atomic as write_atomic
from rastro.domain.jobs import MAX_JOB_BYTES
from rastro.domain.models import ConfigurationError

SUFFIXES = frozenset({".toml", ".json"})
PAGE_SUFFIXES = frozenset({".html", ".htm"})
_NEW_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
MAX_FILES = 500
MAX_DEPTH = 4
SKIPPED_DIRS = {"node_modules", "__pycache__", "venv", "dist", "build", "site"}


def _safe_relative(raw: str, suffixes: frozenset[str]) -> Path:
    if not isinstance(raw, str) or not raw or len(raw) > 1024 or "\x00" in raw or "\\" in raw:
        raise ConfigurationError("Invalid file path")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in suffixes:
        raise ConfigurationError(
            f"Files must be relative {' or '.join(sorted(suffixes))} paths inside the root"
        )
    return rel


def _walk_no_links(root: Path, rel: Path) -> Path:
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ConfigurationError("Symbolic links are not followed")
    return current


def resolve_in_root(root: Path, raw: str, suffixes: frozenset[str] = SUFFIXES) -> Path:
    """Return the existing file path under ``root``; refuse escapes and symlinks."""
    current = _walk_no_links(root, _safe_relative(raw, suffixes))
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("File does not exist inside the root")
    return current


def new_job_path(root: Path, raw: str) -> Path:
    """A path for a new ``.toml`` job in an existing directory under ``root``.

    Each segment is a plain name (letters, digits, ``_``, ``.``, ``-``); the file must not
    exist yet and no component may be a symlink.
    """
    rel = _safe_relative(raw, frozenset({".toml"}))
    if not all(_NEW_NAME.fullmatch(part) for part in rel.parts) or len(rel.parts) > MAX_DEPTH:
        raise ConfigurationError("Job names use letters, digits, '_', '.' and '-' only")
    path = _walk_no_links(root, rel)
    if not path.parent.is_dir() or not path.parent.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("The job file's folder must exist inside the root")
    if path.exists():
        raise ConfigurationError("Job file already exists; refusing to overwrite it")
    return path


def _visible(name: str) -> bool:
    return not name.startswith(".") and name not in SKIPPED_DIRS


def _looks_like_job(path: Path) -> bool:
    """A bounded TOML or JSON object with a top-level ``format_version``: a job, valid or not.

    Invalid jobs stay listed so Studio can show their errors; reports, ``pyproject.toml``
    and other TOML or JSON files do not.
    """
    try:
        text = read_job_bytes(path).decode("utf-8")
        data = json.loads(text) if path.suffix.lower() == ".json" else tomllib.loads(text)
    except (ConfigurationError, OSError, ValueError, RecursionError):
        return False  # ValueError covers UnicodeDecodeError, TOML and JSON decode errors.
    return isinstance(data, dict) and "format_version" in data


def list_job_files(root: Path) -> tuple[list[str], bool]:
    """Relative ``.toml``/``.json`` job files below ``root``; whether the list hit the cap.

    Hidden and vendor directories and symlinks are skipped; the walk is bounded.
    """
    return _listing(root, lambda path: path.suffix.lower() in SUFFIXES and _looks_like_job(path))


def list_pages(root: Path) -> tuple[list[str], bool]:
    """Relative saved ``.html``/``.htm`` pages below ``root``, bounded like the job list."""
    return _listing(root, lambda path: path.suffix.lower() in PAGE_SUFFIXES)


def _listing(root: Path, wanted: Callable[[Path], bool]) -> tuple[list[str], bool]:
    found: list[str] = []
    for directory, subdirs, names in os.walk(root, followlinks=False):
        depth = len(Path(directory).relative_to(root).parts)
        subdirs[:] = sorted(
            d
            for d in subdirs
            if depth < MAX_DEPTH and _visible(d) and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            if len(found) > MAX_FILES:
                break
            if not path.is_symlink() and wanted(path):
                found.append(path.relative_to(root).as_posix())
    found.sort()
    return found[:MAX_FILES], len(found) > MAX_FILES


def read_job_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_JOB_BYTES + 1)
    if len(raw) > MAX_JOB_BYTES:
        raise ConfigurationError("Job file exceeds 256 KiB")
    return raw


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
