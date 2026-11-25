"""The CLI reads schedules in words, compares dataset copies and writes XLSX."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from rastro.cli import main


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(Path("examples/event-agenda/sample"), tmp_path / "sample")
    return tmp_path / "sample"


def test_job_schedule_prints_spec_words_and_runs(capsys):
    assert main(["job", "schedule", "todos los días a las 8:00"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schedule"] == {"every": "1d", "at": "08:00"}
    assert out["in_words"]["en"] == "every day at 8:00"
    assert len(out["next_runs"]) == 3


def test_dataset_changes_against_copy_and_xlsx(sample, tmp_path, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    db = sample / "agenda.sqlite"
    copy = tmp_path / "older.sqlite"
    shutil.copy(db, copy)
    assert main(["dataset", "changes", str(db), "--against", str(copy)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["new"] == out["removed"] == out["changed"] == []
    assert main(["dataset", "changes", str(db)]) != 0
    assert "Choose --since or --against" in capsys.readouterr().err
    target = tmp_path / "agenda.xlsx"
    assert main(["dataset", "export", str(db), "--format", "xlsx", "--output", str(target)]) == 0
    with zipfile.ZipFile(target) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
