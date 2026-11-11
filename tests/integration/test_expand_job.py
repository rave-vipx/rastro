"""A card with several dates becomes one record per date, idempotently (DATA-004/005, JOB-002)."""

from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.dataset_load import load_results
from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job

JOB = """format_version = 1
[region]
locale = "es-UY"
languages = ["es-UY", "es"]
timezone = "America/Montevideo"
[dataset]
name = "multi"
path = "multi.sqlite"
identity = ["title", "starts_at"]
[[sources]]
kind = "file"
path = "card.html"
url = "https://example.test/card"
[sources.recipe]
version = 1
strategy = "css"
record_selector = "div.c"
require_evidence = true
[sources.recipe.fields]
title = "h2::text"
starts_at = "expand:time::text"
[[fields]]
name = "title"
type = "string"
[[fields]]
name = "starts_at"
type = "datetime"
localized = true
"""
CARD = (
    '<div class="c"><h2>Obra A</h2><time>Sab 4 DIC 2027 20:00</time>'
    "<time>Dom 5 DIC 2027 20:00</time><time>Lun 6 DIC 2027 20:00</time></div>"
)


def test_three_dates_load_as_three_records_twice(tmp_path: Path):
    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "card.html").write_text(CARD)
    job = load_job(tmp_path / "job.toml")
    store = SQLiteDatasetStore(tmp_path / "multi.sqlite")
    names = [f.name for f in job.fields]
    for _ in range(2):
        report = run_job(job, base_dir=tmp_path)
        assert len(report.results[0].records) == 3
        load_results(job.dataset, names, report, store)
        records = store.records(job.dataset.name)
        assert len(records) == 3
        assert len({r.fields["starts_at"] for r in records}) == 3
