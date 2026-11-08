"""Load/export boundaries and reports: DATA-001/003, SYNC-006/008, AGENT-003."""

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.dataset_load import load_results
from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job
from rastro.cli import main
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.job_load import job_load_json_schema
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import Outcome


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(Path("examples/uy-dance-agenda/sample"), tmp_path / "sample")
    return tmp_path / "sample"


def test_export_status_and_schema(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stale_sources"] == []
    for command, field in [("export", "records"), ("status", "sources")]:
        assert main(["dataset", command, str(sample / "agenda.sqlite")]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data_trust"] == "untrusted_source_data"
        assert len(payload[field]) == (6 if command == "export" else 2)
        assert main(["dataset", command, str(sample / "agenda.sqlite"), "--format", "jsonl"]) == 0
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(lines) == len(payload[field])
        assert all(line["data_trust"] == "untrusted_source_data" for line in lines)
        if command == "export":
            assert all(line["provenance"] and line["last_observed_at"] for line in lines)
    assert main(["job", "schema", "--target", "load"]) == 0
    assert json.loads(capsys.readouterr().out) == job_load_json_schema()
    assert (
        json.loads(Path("docs/schemas/job-load.schema.json").read_text()) == job_load_json_schema()
    )


def test_only_approved_observed_fields_load(sample):
    job = load_job(sample / "job.toml")
    spec = job.dataset.model_copy(update={"approve": ("performance_id", "title")})
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    report = run_job(job, base_dir=sample)
    load_results(spec, [f.name for f in job.fields], report, store)
    assert all(set(r.fields) == {"performance_id", "title"} for r in store.records(spec.name))


@pytest.mark.parametrize("status", [Outcome.ACCESS_RESTRICTION, Outcome.TRANSIENT_FAILURE])
def test_failed_or_blocked_source_retains_origins(sample, status):
    job = load_job(sample / "job.toml")
    report = run_job(job, base_dir=sample)
    spec = job.dataset
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    names = [f.name for f in job.fields]
    load_results(spec, names, report, store)
    before = store.records(spec.name)
    failed = report.results[0].model_copy(
        update={
            "status": status,
            "records": (),
            "completed_at": report.results[0].completed_at + timedelta(seconds=1),
        }
    )
    result = load_results(
        spec,
        names,
        JobRunResult.from_results(
            schema_fingerprint=report.job.schema_fingerprint, results=[failed]
        ),
        store,
    )
    assert result.exit_code == 1
    assert result.stale_sources[0].reason == status
    assert result.stale_sources[0].last_success == report.results[0].completed_at
    assert result.stale_sources[0].last_success < result.stale_sources[0].last_attempt
    assert set(result.stale_sources[0].affected_keys) == {
        r.key for r in before if report.results[0].source_url in r.origins
    }
    assert store.records(spec.name) == before
    assert all(n == 0 for n in result.sources[0].counts.values())


def test_partial_snapshot_is_not_complete(sample):
    job = load_job(sample / "job.toml")
    report = run_job(job, base_dir=sample)
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    names = [f.name for f in job.fields]
    load_results(job.dataset, names, report, store)
    partial = report.results[0].model_copy(
        update={
            "status": Outcome.PARTIAL_SUCCESS,
            "records": report.results[0].records[:1],
            "completed_at": report.results[0].completed_at + timedelta(seconds=1),
        }
    )
    loaded = load_results(
        job.dataset,
        names,
        JobRunResult.from_results(
            schema_fingerprint=report.job.schema_fingerprint, results=[partial]
        ),
        store,
    )
    assert loaded.exit_code == 3
    assert loaded.sources[0].counts["missing"] == 0
    assert loaded.sources[0].counts["uncertain"] == 3
    assert loaded.stale_sources[0].reason == "partial_snapshot"
    assert loaded.stale_sources[0].last_success == report.results[0].completed_at
    assert loaded.stale_sources[0].last_success < loaded.stale_sources[0].last_attempt
    assert {key.identity for key in loaded.stale_sources[0].affected_keys} == {
        ("candombe-1",),
        ("candombe-2",),
        ("gala-sodre",),
        ("suite-sodre",),
    }
    assert len(store.records(job.dataset.name)) == 6


def test_unobserved_identity_fails_without_publication(sample):
    job = load_job(sample / "job.toml")
    report = run_job(job, base_dir=sample)
    record = report.results[0].records[0]
    evidence = {k: v for k, v in record.evidence.items() if k != "performance_id"}
    record = record.model_copy(update={"evidence": evidence})
    source = report.results[0].model_copy(update={"records": (record,)})
    report = JobRunResult.from_results(
        schema_fingerprint=report.job.schema_fingerprint, results=[source]
    )
    with pytest.raises(DatasetStorageError, match="identity"):
        load_results(
            job.dataset,
            [f.name for f in job.fields],
            report,
            SQLiteDatasetStore(sample / "agenda.sqlite"),
        )
    assert not (sample / "agenda.sqlite").exists()


def test_missing_dataset_block_and_read_paths_fail_explicitly(sample, capsys):
    path = sample / "job.toml"
    text = path.read_text()
    path.write_text(text[: text.index("[dataset]")] + text[text.index("[[sources]]") :])
    assert main(["job", "load", str(path)]) == 2
    assert "[dataset]" in json.loads(capsys.readouterr().err)["message"]
    assert main(["dataset", "export", str(sample / "absent.sqlite")]) == 2
    assert not (sample / "absent.sqlite").exists()


def test_dataset_symlink_is_refused(sample, capsys):
    target = sample / "target.sqlite"
    target.write_text("untouched")
    (sample / "agenda.sqlite").symlink_to(target)
    assert main(["job", "load", str(sample / "job.toml")]) == 2
    assert target.read_text() == "untouched"


def test_multiple_datasets_require_name(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    status = store.source_status("uy_dance_agenda")[0]
    store.mark_source_unavailable("other", status.source_url, status.last_attempt, "unavailable")
    assert main(["dataset", "status", str(sample / "agenda.sqlite")]) == 2
    capsys.readouterr()
    assert main(["dataset", "status", str(sample / "agenda.sqlite"), "--dataset", "other"]) == 0
    assert json.loads(capsys.readouterr().out)["stale_sources"][0]["last_success"] is None


def test_unknown_dataset_is_not_successful_empty_output(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    assert main(["dataset", "export", str(sample / "agenda.sqlite"), "--dataset", "typo"]) == 2
    assert not capsys.readouterr().out


def sale_date_swap(sample):
    import re

    from rastro.mocklab.mutations import HtmlChangeLabel, MutationPlan

    seller = sample / "tickantel-evento.html"
    body = seller.read_text(encoding="utf-8")
    changes = tuple(
        HtmlChangeLabel(
            selector='script[type="application/ld+json"]',
            match=f'"startDate": "{date}"',
            text='"startDate": "2026-11-01T10:00:00-03:00"',
        )
        for date in re.findall(r'"startDate": "([^"]+)"', body)
    )
    seller.write_text(
        MutationPlan(seed=42, operators=changes).apply(body).document, encoding="utf-8"
    )


def test_sale_date_swap_is_held_and_old_rows_kept(sample):
    job = load_job(sample / "job.toml")
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    names = [f.name for f in job.fields]
    report = run_job(job, base_dir=sample)
    load_results(job.dataset, names, report, store)
    before = {r.key: r for r in store.records(job.dataset.name)}
    sale_date_swap(sample)
    changed = run_job(job, base_dir=sample)
    held = load_results(job.dataset, names, changed, store, rules=job.consistency)
    assert held.exit_code == 3
    assert held.sources[1].consistency.verdict == "inconsistent"
    assert all(n == 0 for n in held.sources[1].counts.values())
    after = {r.key: r for r in store.records(job.dataset.name)}
    for key, record in before.items():
        if changed.results[1].source_url in record.origins:
            url = changed.results[1].source_url
            assert after[key].origins[url] == record.origins[url]


@pytest.mark.parametrize("history_only", [False, True])
def test_consistent_load_unchanged_and_no_rules_means_no_gate(sample, history_only):
    job = load_job(sample / "job.toml")
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    names = [f.name for f in job.fields]
    rules = job.consistency[-1:] if history_only else job.consistency
    loaded = load_results(job.dataset, names, run_job(job, base_dir=sample), store, rules=rules)
    assert loaded.exit_code == 0
    expected = "not_assessed" if history_only else "consistent"
    assert loaded.sources[0].consistency.verdict == expected
    assert loaded.sources[1].consistency.verdict == expected
    sale_date_swap(sample)
    loaded = load_results(job.dataset, names, run_job(job, base_dir=sample), store)
    assert loaded.exit_code == 0
    assert all(s.consistency is None for s in loaded.sources)
    assert any(
        r.fields["starts_at"].startswith("2026-11-01") for r in store.records(job.dataset.name)
    )


def test_held_source_reported_stale_with_consistency_failed_and_exit_3(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    sale_date_swap(sample)
    assert main(["job", "load", str(sample / "job.toml")]) == 3
    payload = json.loads(capsys.readouterr().out)
    held = payload["sources"][1]
    assert held["consistency"]["verdict"] == "inconsistent"
    assert any(
        r["kind"] == "after_observation" and r["status"] == "violated"
        for r in held["consistency"]["results"]
    )
    assert held["next_action"] == "rastro recipe repair"
    assert payload["stale_sources"][0]["reason"] == "consistency_failed"
    assert payload["stale_sources"][0]["affected_keys"]


@pytest.fixture
def price_job(tmp_path):
    """Synthetic numeric history for QUAL-005 and DRIFT-002 regression proofs."""
    path = tmp_path / "job.json"
    config = {
        "format_version": 1,
        "recipe_memory": "recipes.sqlite",
        "dataset": {"name": "prices", "path": "data.sqlite", "identity": ["id"]},
        "fields": [{"name": "id", "type": "string"}, {"name": "price", "type": "number"}],
        "consistency": [
            dict(
                kind="historical_shift",
                field="price",
                unit="ratio",
                max_shift=0.6,
                min_history=1,
            )
        ],
        "sources": [
            {
                "kind": "file",
                "path": f"{name}.html",
                "url": f"https://example.test/{name}",
                "recipe": {
                    "strategy": "css",
                    "record_selector": "article",
                    "fields": {"id": "::attr(data-id)", "price": "span::text"},
                },
            }
            for name in "ab"
        ],
    }
    path.write_text(json.dumps(config))
    for name in "ab":
        write_prices(tmp_path, name, 100)
    return path


def write_prices(folder, name, price):
    (folder / f"{name}.html").write_text(
        "<main>"
        + "".join(f'<article data-id="{name}{i}"><span>{price}</span></article>' for i in range(3))
        + "</main>"
    )


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_history_gate_uses_pre_load_history(price_job, capsys, order):
    config = json.loads(price_job.read_text())
    if order == "ba":
        config["sources"].reverse()
    price_job.write_text(json.dumps(config))
    assert main(["job", "load", str(price_job)]) == 0
    capsys.readouterr()
    write_prices(price_job.parent, "a", 150)
    write_prices(price_job.parent, "b", 165)
    assert main(["job", "load", str(price_job)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert {s["source_url"][-1]: s["consistency"]["verdict"] for s in report["sources"]} == {
        "a": "consistent",
        "b": "inconsistent",
    }
    rows = SQLiteDatasetStore(price_job.parent / "data.sqlite").records("prices")
    assert {r.fields["price"] for r in rows if r.key.identity[0].startswith("b")} == {100}


def test_held_load_cannot_become_drift_baseline(price_job, capsys):
    args = ["job", "load", str(price_job), "--drift"]
    assert main(args) == 0
    capsys.readouterr()
    with closing(sqlite3.connect(price_job.parent / "data.sqlite")) as db:
        before = db.execute("SELECT run FROM drift_runs WHERE successful = 1").fetchall()
    write_prices(price_job.parent, "a", 1000)
    assert main(args) == 3
    capsys.readouterr()
    with closing(sqlite3.connect(price_job.parent / "data.sqlite")) as db:
        assert db.execute("SELECT run FROM drift_runs WHERE successful = 0").fetchall()
    from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
    from rastro.domain.drift import StructuralSnapshot

    with closing(sqlite3.connect(price_job.parent / "data.sqlite")) as db:
        payload = db.execute("SELECT snapshot FROM drift_runs WHERE successful = 0").fetchone()[0]
    held = StructuralSnapshot.model_validate_json(payload)
    baseline = SQLiteDriftMonitor(price_job.parent / "data.sqlite").baseline("prices", held)
    assert baseline.run_id in {r[0] for r in before}


def test_partial_load_reports_rejected_candidates(sample):
    """QUAL-006: three valid candidates and one rejection give validity 0.75."""
    job = load_job(sample / "job.toml")
    report = run_job(job, base_dir=sample)
    source = report.results[0]
    partial = source.model_copy(
        update={
            "status": Outcome.PARTIAL_SUCCESS,
            "records": source.records[:3],
            "metrics": source.metrics.model_copy(update={"rejected": 1}),
        }
    )
    loaded = load_results(
        job.dataset,
        [f.name for f in job.fields],
        JobRunResult.from_results(
            schema_fingerprint=report.job.schema_fingerprint, results=[partial]
        ),
        SQLiteDatasetStore(sample / "agenda.sqlite"),
        rules=job.consistency,
    )
    assert loaded.sources[0].consistency.structural_validity == 0.75


def test_sodre_title_trap_extracts_venue_and_is_held(tmp_path, capsys):
    from benchmarks.repair_suite.mutations import SODRE, VENUE

    shutil.copytree(Path("benchmarks/repair_suite/cases/sodre"), tmp_path / "sodre")
    folder = tmp_path / "sodre"
    path = folder / "job.toml"
    assert main(["job", "load", str(path)]) == 0
    capsys.readouterr()
    page = folder / "page.html"
    trap = next(c for c in SODRE if c.name == "title_selector_hits_venue")
    page.write_text(trap.transform(page.read_text(encoding="utf-8")), encoding="utf-8")
    report = run_job(load_job(path), base_dir=folder)
    assert {r.data.title for r in report.results[0].records} == {VENUE}
    assert main(["job", "load", str(path)]) == 3
    held = json.loads(capsys.readouterr().out)["sources"][0]
    assert held["consistency"]["verdict"] == "inconsistent"


def test_repair_suite_readme_matches_measured_counts():
    """DIFF-002: published claims must match the measured artifact."""
    folder = Path("benchmarks/repair_suite")
    counts = json.loads((folder / "results.json").read_text())["counts"]
    readme = (folder / "README.md").read_text()
    for outcome, count in counts.items():
        assert f"| {outcome} | {count} |" in readme


def test_html_export_escapes_script_in_values(sample, capsys, monkeypatch):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    records = SQLiteDatasetStore(sample / "agenda.sqlite").records("uy_dance_agenda")
    record = records[0].model_copy(
        update={"fields": {**records[0].fields, "title": "<script>alert(1)</script>"}}
    )
    monkeypatch.setattr(SQLiteDatasetStore, "records", lambda self, dataset: (record,))
    assert main(["dataset", "export", str(sample / "agenda.sqlite"), "--format", "html"]) == 0
    output = capsys.readouterr().out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in output
    assert "<script>" not in output


def test_html_export_links_only_http_urls(sample, capsys, monkeypatch):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    records = SQLiteDatasetStore(sample / "agenda.sqlite").records("uy_dance_agenda")
    fields = {
        **records[0].fields,
        "title": "javascript:alert(1)",
        "ticket_url": "https://example.com/",
    }
    monkeypatch.setattr(
        SQLiteDatasetStore,
        "records",
        lambda self, dataset: (records[0].model_copy(update={"fields": fields}),),
    )
    assert main(["dataset", "export", str(sample / "agenda.sqlite"), "--format", "html"]) == 0
    output = capsys.readouterr().out
    assert 'href="https://example.com/"' in output
    assert 'href="javascript:' not in output
    assert "javascript:alert(1)" in output


def test_upcoming_filter_and_sort_with_missing_values_last(sample, capsys, monkeypatch):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    record = SQLiteDatasetStore(sample / "agenda.sqlite").records("uy_dance_agenda")[0]
    values = ["2099-01-02T00:00:00Z", None, "2000-01-01T00:00:00Z", "2099-01-01T00:00:00Z"]
    records = tuple(
        record.model_copy(update={"fields": {**record.fields, "starts_at": v}}) for v in values
    )
    monkeypatch.setattr(SQLiteDatasetStore, "records", lambda self, dataset: records)
    args = ["dataset", "export", str(sample / "agenda.sqlite"), "--sort-field", "starts_at"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)["records"]
    assert [r["fields"]["starts_at"] for r in result] == [values[2], values[3], values[0], None]
    assert main([*args, "--upcoming"]) == 0
    result = json.loads(capsys.readouterr().out)["records"]
    assert [r["fields"]["starts_at"] for r in result] == [values[3], values[0]]


def test_html_marks_stale_source_rows(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    status = store.source_status("uy_dance_agenda")[0]
    store.mark_source_unavailable(
        "uy_dance_agenda",
        status.source_url,
        status.last_attempt + timedelta(seconds=1),
        "unavailable",
    )
    assert main(["dataset", "export", str(sample / "agenda.sqlite"), "--format", "html"]) == 0
    assert "<td>stale</td>" in capsys.readouterr().out


def test_json_export_unchanged_without_new_flags(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    path = sample / "agenda.sqlite"
    assert main(["dataset", "export", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "dataset": "uy_dance_agenda",
        "records": [
            r.model_dump(mode="json") for r in SQLiteDatasetStore(path).records("uy_dance_agenda")
        ],
    }


def test_html_empty_result_retains_columns(sample, capsys, monkeypatch):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    record = SQLiteDatasetStore(sample / "agenda.sqlite").records("uy_dance_agenda")[0]
    record = record.model_copy(update={"fields": {**record.fields, "starts_at": "2000-01-01"}})
    monkeypatch.setattr(SQLiteDatasetStore, "records", lambda self, dataset: (record,))
    assert (
        main(
            [
                "dataset",
                "export",
                str(sample / "agenda.sqlite"),
                "--format",
                "html",
                "--sort-field",
                "starts_at",
                "--upcoming",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "<th>starts_at</th>" in output
    assert "No records" in output


def test_upcoming_rejects_non_date_and_requires_sort_field(sample, capsys):
    assert main(["job", "load", str(sample / "job.toml")]) == 0
    capsys.readouterr()
    path = str(sample / "agenda.sqlite")
    assert main(["dataset", "export", path, "--upcoming"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_input"
    assert main(["dataset", "export", path, "--sort-field", "title", "--upcoming"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_input"
