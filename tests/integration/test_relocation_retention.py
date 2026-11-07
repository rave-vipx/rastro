"""REPAIR-002, SCR-002: additive retention, last success survives failed loads."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from rastro.cli import main

SAMPLE = Path(__file__).parents[2] / "examples/uy-dance-agenda/sample"


def test_job_load_retains_last_success_and_does_not_change_memory_schema(tmp_path, capsys):
    shutil.copytree(SAMPLE, tmp_path / "sample")
    path = tmp_path / "sample/job.toml"
    assert main(["job", "load", str(path)]) == 0
    capsys.readouterr()
    with closing(sqlite3.connect(path.parent / "agenda.sqlite")) as db:
        baseline = db.execute("SELECT snapshot, fixture FROM relocation_samples").fetchall()
        assert len(baseline) == 2
        assert json.loads(baseline[0][1])["expected"]["accepted"] > 0
        (path.parent / "sodre-cartelera.html").write_text(
            "<html>Unavailable</html>", encoding="utf-8"
        )
        assert main(["job", "load", str(path)]) == 3
        capsys.readouterr()
        # The failed source retains its previous fingerprints and fixture.
        current = db.execute("SELECT snapshot, fixture FROM relocation_samples").fetchall()
        assert baseline[0] == current[0]
        assert db.execute("SELECT version FROM relocation_schema").fetchall() == [(1,)]
