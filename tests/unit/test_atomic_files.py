"""SEC-008: atomic writes replace the file and sync the directory entry."""

import os

import pytest

from rastro.application import atomic_files


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX only")
def test_write_atomic_syncs_the_parent_directory(tmp_path, monkeypatch):
    target = tmp_path / "job.toml"
    target.write_text("old", encoding="utf-8")
    synced = []
    monkeypatch.setattr(atomic_files, "fsync_directory", synced.append)
    atomic_files.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    assert synced == [tmp_path]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["job.toml"]


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX only")
def test_fsync_directory_opens_and_syncs(tmp_path):
    atomic_files.fsync_directory(tmp_path)
