"""Offline proof that one job reads three venue pages (SYNC-002, DATA-004/005)."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "uy-dance-agenda"
V03 = EXAMPLE / "sample" / "v03"


@pytest.fixture
def offline_job(tmp_path: Path) -> Path:
    """The offline three-venue job, copied with its synthetic pages."""
    for item in V03.iterdir():
        shutil.copyfile(item, tmp_path / item.name)
    return tmp_path / "job.toml"


def _load(job: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    code = main(["job", "load", str(job)])
    captured = capsys.readouterr()
    assert code in (0, 3), captured.err
    return json.loads(captured.out)


def _records(job: Path) -> dict[str, list[dict]]:
    store = SQLiteDatasetStore(job.parent / "agenda.sqlite")
    by_venue: dict[str, list[dict]] = {}
    for record in store.records("uy_dance_agenda"):
        by_venue.setdefault(record.fields["venue"], []).append(record.fields)
    return by_venue


def test_offline_job_declares_dataset_and_new_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["job", "validate", str(V03 / "job.toml")]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["source_count"] == 3
    assert {"starts_on", "starts_at", "category"} <= set(validated["fields"])
    dataset = validated["job"]["dataset"]
    assert dataset["identity"] == ["title", "venue", "starts_on"]


def test_three_venue_pages_load_with_per_source_counts(
    offline_job: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _load(offline_job, capsys)
    assert [s["counts"]["created"] for s in report["sources"]] == [3, 4, 3]
    assert [s["status"] for s in report["sources"]] == ["partial_success", "success", "success"]
    by_venue = _records(offline_job)
    assert {v: len(r) for v, r in by_venue.items()} == {
        "Sala Uno Sintética": 1,
        "Sala Dos Sintética": 2,
        "Sala Sintética": 4,
        "Sala Inventada": 3,
    }


def test_sodre_cards_use_first_date_and_reject_missing_venue(
    offline_job: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _load(offline_job, capsys)
    by_title = {r["title"]: r for rows in _records(offline_job).values() for r in rows}
    assert "Gala sin sede" not in by_title
    ballet = by_title["Ballet del Sur"]
    assert ballet["starts_on"] == "2026-12-10"
    assert ballet["starts_at"].startswith("2026-12-10T20:00:00")
    assert ballet["category"] == "Danza"
    double = by_title["Charla doble"]
    assert double["starts_at"].startswith("2026-11-26T19:00:00")
    assert double["ticket_url"] == "https://sodre.gub.uy/inicio/espectaculo/3/charla-doble"


def test_venue_pages_without_time_keep_starts_at_empty(
    offline_job: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _load(offline_job, capsys)
    by_venue = _records(offline_job)
    for venue in ("Sala Sintética", "Sala Inventada"):
        assert all("starts_at" not in r for r in by_venue[venue])
    zita = {r["title"]: r for r in by_venue["Sala Sintética"]}
    assert date.fromisoformat(zita["Milonga de prueba"]["starts_on"]).day == 14
    assert zita["Milonga de prueba"]["ticket_url"] == "https://sala.example.test/espectaculo/a-2/"
    mag = {r["title"]: r for r in by_venue["Sala Inventada"]}
    assert date.fromisoformat(mag["Monólogo en tres noches"]["starts_on"]).day == 27
    assert "performers" not in mag["Candombe de cierre"]


def test_second_load_creates_nothing(offline_job: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _load(offline_job, capsys)
    second = _load(offline_job, capsys)
    assert sum(s["counts"]["created"] for s in second["sources"]) == 0
    assert sum(s["counts"]["unchanged"] for s in second["sources"]) == 10
