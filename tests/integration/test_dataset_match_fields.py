"""PR 61 review regressions: DATA-001/002/003, SYNC-008."""

import shutil
from pathlib import Path

import pytest

from rastro.application.job_loader import load_job
from rastro.cli import main
from rastro.domain.models import ConfigurationError


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), tmp_path / "sample")
    return tmp_path / "sample"


@pytest.mark.parametrize(
    "settings",
    [
        'match_fields = ["title", "title", "nonexistent_field"]\n'
        'approve = ["performance_id", "title"]',
        'match_fields = ["title", "title", "starts_at"]',
        'match_fields = ["title", "venue", "nonexistent_field"]',
        'match_fields = ["title", "venue", ""]',
        'match_fields = ["title", "venue", "starts_at"]\napprove = ["performance_id", "title"]',
    ],
)
def test_explicit_match_fields_fail_closed(sample, capsys, settings):
    path = sample / "job.toml"
    path.write_text(
        path.read_text().replace('match_fields = ["title", "venue", "starts_at"]', settings)
    )
    with pytest.raises(ConfigurationError, match=r"dataset\.match_fields"):
        load_job(path)
    for command in ("validate", "preview", "load"):
        assert main(["job", command, str(path)]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "match_fields" in captured.err
    assert not (sample / "agenda.sqlite").exists()
