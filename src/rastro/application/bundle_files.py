"""Bounded, symlink-refusing bundle file I/O shared by export, import and the registry (HUB-001).

Bundles are untrusted input: every file is size-bounded and hashed before it is trusted.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.recipe_bundle import (
    MANIFEST_FILE,
    MAX_METADATA_BYTES,
    SAMPLE_FILES,
    SUPPORTED_BUNDLE_FORMATS,
    BundleIntegrityError,
    BundleManifest,
    BundleVersionError,
    sha256_hex,
)


def read_bundle(directory: Path, limits: Limits) -> tuple[BundleManifest, dict[str, bytes]]:
    if directory.is_symlink() or not directory.is_dir():
        raise BundleIntegrityError("Bundle path must be a real directory")
    raw = load_json(read_bounded(directory / MANIFEST_FILE, MAX_METADATA_BYTES, bundle=True))
    if not isinstance(raw, dict):
        raise BundleIntegrityError("Bundle manifest must be an object")
    if raw.get("format_version") not in SUPPORTED_BUNDLE_FORMATS:
        raise BundleVersionError("Unsupported bundle format; this release reads formats 1 and 2")
    try:
        manifest = BundleManifest.model_validate(raw)
    except ValidationError as error:
        raise BundleIntegrityError("Bundle manifest is invalid") from error
    if set(manifest.files) != manifest.expected_names():
        raise BundleIntegrityError("Bundle lists unexpected files")
    sample_name = SAMPLE_FILES[manifest.representation]
    files: dict[str, bytes] = {}
    for name, digest in manifest.files.items():
        cap = limits.max_input_bytes(manifest.representation) if name == sample_name else None
        data = read_bounded(directory / name, cap or MAX_METADATA_BYTES, bundle=True)
        if sha256_hex(data) != digest:
            raise BundleIntegrityError("Bundle file does not match its recorded digest")
        files[name] = data
    return manifest, files


def read_bounded(path: Path, cap: int, *, bundle: bool = False) -> bytes:
    error = BundleIntegrityError if bundle else ConfigurationError
    if path.is_symlink():  # O_NOFOLLOW does not exist on Windows
        raise error("File is missing, linked or unreadable")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise error("File must be a regular file")
            data = handle.read(cap + 1)
    except OSError as cause:
        raise error("File is missing, linked or unreadable") from cause
    if len(data) > cap:
        raise error("File exceeds its size limit")
    return data


def decode_sample(data: bytes, *, bundle: bool) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise (BundleIntegrityError if bundle else ConfigurationError)(
            "The sample file is not valid UTF-8"
        ) from error


def load_json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (ValueError, RecursionError) as error:
        raise BundleIntegrityError("Bundle file is not valid JSON") from error


def write_all(directory: Path, contents: dict[str, bytes]) -> None:
    if directory.is_symlink():
        raise ConfigurationError("Bundle directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    for name, data in contents.items():
        try:
            with os.fdopen(os.open(directory / name, flags, 0o644), "wb") as handle:
                handle.write(data)
        except OSError as cause:
            raise ConfigurationError("Bundle file cannot be written") from cause
