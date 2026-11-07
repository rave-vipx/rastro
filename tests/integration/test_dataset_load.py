"""Load/export boundaries and reports: DATA-001/003, SYNC-006/008, AGENT-003."""

import json
import shutil
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
