"""REPAIR-002/JOB-001: Spanish sample data and reports use UTF-8 in other locales."""

import os
import runpy
from pathlib import Path

import pytest
from test_recipe_cli import setup_sample

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def test_repair_report_explicitly_uses_utf8(tmp_path, capsys, monkeypatch):
    _, job = setup_sample(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    fdopen = os.fdopen
    encodings = []

    def locale_fdopen(fd, mode="r", *args, **kwargs):
        if "b" not in mode:
            kwargs.setdefault("encoding", "cp1252")
            encodings.append(kwargs["encoding"])
        return fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", locale_fdopen)
    main(["recipe", "repair", str(job)])
    assert encodings == ["utf-8"]


def test_repair_demo_preserves_spanish_text_under_cp1252_default(tmp_path, monkeypatch, capsys):
    demo = runpy.run_path("examples/uy-dance-agenda/repair_demo.py")
    read_text, write_text = Path.read_text, Path.write_text

    def locale_read(path, encoding=None, errors=None):
        return read_text(path, encoding=encoding or "cp1252", errors=errors)

    def locale_write(path, data, encoding=None, errors=None, newline=None):
        return write_text(path, data, encoding=encoding or "cp1252", errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", locale_read)
    monkeypatch.setattr(Path, "write_text", locale_write)
    folder = tmp_path / "sample"
    demo["run_demo"](folder)
    assert "All assertions passed" in capsys.readouterr().out
    records = SQLiteDatasetStore(folder / "agenda.sqlite").records("uy_dance_agenda")
    assert any("Área Tango" in str(r.fields["title"]) for r in records)
