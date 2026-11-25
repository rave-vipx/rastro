"""PR 61 review regressions."""

import json
import shutil
from pathlib import Path

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.job_loader import load_job
from rastro.cli import main


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(Path("examples/event-agenda/sample"), tmp_path / "sample")
    return tmp_path / "sample"


@pytest.mark.parametrize("version", [".", "v2"])
def test_omitted_performer_uses_fallback_and_loads(sample, capsys, version):
    root = sample / version
    tick = root / "tickets-event.html"
    tick.write_text(
        tick.read_text(encoding="utf-8").replace('"performer":', '"_omitted_performer":'),
        encoding="utf-8",
    )
    job = load_job(root / "job.toml")
    for source in job.sources:
        assert "performance_id" in source.recipe.fallbacks[0].fields
    assert main(["job", "load", str(root / "job.toml")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sources"][1]["status"] == "success"
    assert report["stale_sources"] == []
    records = SQLiteDatasetStore(root / "agenda.sqlite").records("event_agenda")
    assert len(records) == 6
    seller_origins = [o for r in records for url, o in r.origins.items() if "tickets" in url]
    assert len(seller_origins) == 3
    assert all(o.provenance["performers"].locator.endswith("/description") for o in seller_origins)
    assert all(o.fields["performance_id"] for o in seller_origins)
