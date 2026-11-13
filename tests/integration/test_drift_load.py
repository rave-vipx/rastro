"""Drift reaches the dataset and preserves earlier observations (DRIFT-001-006)."""

import shutil
from pathlib import Path

import pytest

from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.dataset_load import load_results
from rastro.application.drift import collect_snapshots
from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job
from rastro.mocklab.mutations.html_content import HtmlChangeClasses
from rastro.mocklab.mutations.plan import MutationPlan


def load(sample):
    job = load_job(sample / "job.toml")
    with collect_snapshots(ScraplingDriftProbe()) as snapshots:
        report = run_job(job, base_dir=sample)
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    loaded = load_results(
        job.dataset,
        [f.name for f in job.fields],
        report,
        store,
        monitor=SQLiteDriftMonitor(sample / "agenda.sqlite"),
        snapshots=snapshots,
    )
    return loaded, store


def test_renamed_class_yields_structural_drift_naming_field_and_table_effect(tmp_path):
    sample = tmp_path / "sample"
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), sample)
    first, store = load(sample)
    before = store.records(first.dataset)
    path = sample / "sodre-cartelera.html"
    mutation = MutationPlan(
        seed=7,
        operators=(HtmlChangeClasses(selector=".cyg--dia", rename={"cyg--dia": "date-new"}),),
    )
    path.write_text(mutation.apply(path.read_text(encoding="utf-8")).document, encoding="utf-8")
    second, store = load(sample)
    drift = second.drift[0]
    assert drift.verdict == "structural_drift"
    assert any(s.field == "starts_at" and "kept" in s.dataset_effect for s in drift.signals)
    assert any(
        s.field == "starts_at"
        and s.indicator == "selector_match_count"
        and s.baseline == 4
        and s.current == 0
        for s in drift.signals
    )
    assert second.stale_sources
    old = {r.key: r for r in before}
    for row in store.records(first.dataset):
        for source in second.stale_sources:
            if source.source_url in row.origins:
                assert row.origins[source.source_url] == old[row.key].origins[source.source_url]
    assert len(store.records(first.dataset)) == len(before)


def test_total_extraction_failure_after_success_is_high_risk(tmp_path):
    """DRIFT-002/004: a record selector that stops matching is never low risk or 0.0."""
    sample = tmp_path / "sample"
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), sample)
    load(sample)
    path = sample / "sodre-cartelera.html"
    html = path.read_text(encoding="utf-8")
    assert "espectaculo" in html
    path.write_text(html.replace("espectaculo", "show-renamed"), encoding="utf-8")
    second, _ = load(sample)
    drift = second.drift[0]
    assert second.sources[0].status != "success"
    assert drift.risk == "high"
    assert drift.score >= drift.thresholds.high
    assert drift.verdict in {"extraction_failure", "structural_drift"}
    assert any(s.indicator == "extraction_failed" for s in drift.signals)


def test_total_failure_without_drift_baseline_uses_dataset_success_history(tmp_path, capsys):
    """DRIFT-002/004: the earlier success came from a load without --drift; still high risk."""
    import json

    from rastro.cli import main

    sample = tmp_path / "sample"
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), sample)
    command = ["job", "load", str(sample / "job.toml")]
    assert main(command) == 0
    path = sample / "sodre-cartelera.html"
    path.write_text(
        path.read_text(encoding="utf-8").replace("espectaculo", "show-renamed"), encoding="utf-8"
    )
    capsys.readouterr()
    main([*command, "--drift"])
    report = json.loads(capsys.readouterr().out)
    failed, healthy = report["drift"]
    assert (failed["risk"], failed["score"], failed["verdict"]) == (
        "high",
        1.0,
        "extraction_failure",
    )
    assert [s["indicator"] for s in failed["signals"]] == ["extraction_failed"]
    assert failed["signals"][0]["baseline"] == "success"
    assert (healthy["risk"], healthy["score"]) == ("low", 0.0)


def test_first_ever_failure_is_not_called_drift():
    """No earlier success anywhere: nothing to drift from, so no extraction_failed signal."""
    from datetime import UTC, datetime

    from rastro.domain.drift import FieldStructure, StructuralSnapshot, assess_drift

    snapshot = StructuralSnapshot(
        source_url="https://example.org",
        run_id="r1",
        observed_at=datetime(2026, 11, 12, tzinfo=UTC),
        schema_fingerprint="a" * 64,
        status="missing_data",
        fields={"t": FieldStructure(field_coverage=0, validation_failure_rate=0)},
    )
    assert assess_drift(None, snapshot).risk == "low"
    escalated = assess_drift(None, snapshot, previously_succeeded=True)
    assert (escalated.risk, escalated.score) == ("high", 1.0)


def test_cli_drift_is_opt_in_and_persists_without_changing_default_report(tmp_path, capsys):
    import json
    import sqlite3
    from contextlib import closing

    from rastro.cli import main

    sample = tmp_path / "sample"
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), sample)
    command = ["job", "load", str(sample / "job.toml")]
    assert main(command) == 0
    assert "drift" not in json.loads(capsys.readouterr().out)
    assert main([*command, "--drift"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["drift"]) == 2
    assert all(item["verdict"] == "none" for item in first["drift"])
    assert main([*command, "--drift"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert all(item["score"] == 0 for item in second["drift"])
    with closing(sqlite3.connect(sample / "agenda.sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM drift_runs").fetchone()[0] == 4


def test_capture_tracks_selected_fallback_and_resets_after_scope():
    from pydantic import BaseModel

    from rastro import CssRecipe, Rastro, Source
    from rastro.application.drift import snapshot_result

    class Record(BaseModel):
        title: str

    recipe = CssRecipe(
        record_selector="article",
        fields={"title": ".old::text"},
        fallbacks=(CssRecipe(record_selector="article", fields={"title": "b::text"}),),
    )
    client = Rastro()
    with collect_snapshots(ScraplingDriftProbe()) as captured:
        result = client.extract_html(
            "<article><b>Hello</b></article>",
            source=Source(url="https://example.org"),
            schema=Record,
            recipe=recipe,
        )
    snapshot = snapshot_result(result, ["title"], captured=captured[result.job_id])
    assert snapshot.fields["title"].fallback_used is True
    assert snapshot.fields["title"].selector_match_count == 1
    assert snapshot.fields["title"].field_coverage == 1
    before = dict(captured)
    client.extract_html(
        "<article><b>Next</b></article>",
        source=Source(url="https://example.org"),
        schema=Record,
        recipe=recipe,
    )
    assert captured == before


@pytest.mark.asyncio
async def test_capture_propagates_through_async_extraction():
    from pydantic import BaseModel

    from rastro import CssRecipe, Rastro, Source

    class Record(BaseModel):
        title: str

    with collect_snapshots(ScraplingDriftProbe()) as captured:
        result = await Rastro().extract_html_async(
            "<article><b>Hello</b></article>",
            source=Source(url="https://example.org"),
            schema=Record,
            recipe=CssRecipe(record_selector="article", fields={"title": "b::text"}),
        )
    assert captured[result.job_id].fields["title"].selector_match_count == 1


def test_capture_must_belong_to_the_extraction(tmp_path):
    from rastro.application.drift import snapshot_result
    from rastro.domain.models import ConfigurationError

    sample = tmp_path / "sample"
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), sample)
    job = load_job(sample / "job.toml")
    with collect_snapshots(ScraplingDriftProbe()) as snapshots:
        report = run_job(job, base_dir=sample)
    with pytest.raises(ConfigurationError, match="does not belong"):
        snapshot_result(report.results[0], ["title"], captured=snapshots[report.results[1].job_id])
