"""Dataset and recipe-memory files are private to the owner (QUAL-006, SECURITY.md)."""

import os
import stat
from datetime import UTC, datetime

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
AT = datetime(2026, 11, 6, tzinfo=UTC)
URL = "https://venue.example/events"


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_dataset_file_is_created_owner_only(tmp_path):
    path = tmp_path / "data.sqlite"
    SQLiteDatasetStore(path).mark_source_unavailable("agenda", URL, AT, "offline")
    assert mode(path) == 0o600


def test_existing_dataset_and_sidecar_files_are_tightened_on_open(tmp_path):
    path = tmp_path / "data.sqlite"
    store = SQLiteDatasetStore(path)
    store.mark_source_unavailable("agenda", URL, AT, "offline")
    wal = tmp_path / "data.sqlite-wal"
    wal.write_bytes(b"")
    for item in (path, wal):
        item.chmod(0o644)
    assert store.source_status("agenda")
    assert (mode(path), mode(wal)) == (0o600, 0o600)


def test_recipe_memory_is_created_and_tightened_owner_only(tmp_path):
    path = tmp_path / "recipes.sqlite"
    repository = SQLiteRecipeRepository(path)
    repository.prune(before=AT, dry_run=True)
    assert mode(path) == 0o600
    path.chmod(0o644)
    repository.prune(before=AT, dry_run=True)
    assert mode(path) == 0o600
