"""rastro.load_records: caller-built records with job-load semantics (N15; DATA-001,
SYNC-002, SYNC-008, PROV-001).

Synthetic data only. The records are read back through the SQLite dataset adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import rastro
from rastro import ConfigurationError, ResourceLimitError
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore

SOURCE = "https://parties.example/listing"
JOB = """
format_version = 1
[recipe]
strategy = "css"
record_selector = ".party"
[recipe.fields]
slug = ".slug::text"
title = "h2::text"
price = ".price::text"
[[fields]]
name = "slug"
type = "string"
[[fields]]
name = "title"
type = "string"
[[fields]]
name = "price"
type = "decimal"
required = false
[[sources]]
url = "https://parties.example/listing"
[acquisition]
allowed_hosts = ["parties.example"]
[dataset]
name = "parties"
path = "parties.sqlite"
identity = ["slug"]
"""


@pytest.fixture
def job(tmp_path: Path) -> Path:
    path = tmp_path / "job.toml"
    path.write_text(JOB, encoding="utf-8")
    return path


def stored(job: Path) -> dict[str, dict[str, object]]:
    records = SQLiteDatasetStore(job.parent / "parties.sqlite").records("parties")
    return {str(r.fields["slug"]): r.fields for r in records}


def party(slug: str, title: str = "Noche", price: str | None = "450.00") -> dict[str, object]:
    return {"slug": slug, "title": title, "price": price}


def test_load_twice_reports_created_unchanged_updated_and_missing(job):
    first = rastro.load_records(job, [party("a"), party("b")], source_url=SOURCE)
    assert first.status == "success"
    assert first.sources[0].counts["created"] == 2
    second = rastro.load_records(job, [party("a"), party("b", price="500")], source_url=SOURCE)
    counts = second.sources[0].counts
    assert (counts["unchanged"], counts["updated"]) == (1, 1)
    third = rastro.load_records(job, [party("a")], source_url=SOURCE)
    assert third.sources[0].counts["missing"] == 1
    assert stored(job)["b"]["price"] == "500"


def test_provenance_marks_adapter_origin_and_keeps_source_url(job):
    rastro.load_records(job, [party("a")], source_url=SOURCE)
    (record,) = SQLiteDatasetStore(job.parent / "parties.sqlite").records("parties")
    assert record.source_url == SOURCE
    assert set(record.provenance) == {"slug", "title", "price"}
    for evidence in record.provenance.values():
        assert (evidence.method, evidence.locator, evidence.recipe_version) == (
            "json",
            "adapter",
            0,
        )
        assert evidence.source_url == SOURCE


def test_observed_at_is_preserved_and_defaults_to_load_time(job):
    when = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    rastro.load_records(job, [party("a")], source_url=SOURCE, observed_at=when)
    (record,) = SQLiteDatasetStore(job.parent / "parties.sqlite").records("parties")
    assert record.provenance["slug"].observed_at == when
    before = datetime.now(UTC)
    rastro.load_records(job, [party("a")], source_url=SOURCE)
    (record,) = SQLiteDatasetStore(job.parent / "parties.sqlite").records("parties")
    assert record.last_observed_at >= before
    assert record.first_observed_at == when


def test_optional_field_left_out_is_not_loaded(job):
    rastro.load_records(job, [{"slug": "a", "title": "Noche"}], source_url=SOURCE)
    (record,) = SQLiteDatasetStore(job.parent / "parties.sqlite").records("parties")
    assert set(record.fields) == {"slug", "title"}


@pytest.mark.parametrize(
    ("records", "match"),
    [
        ([{"title": "Noche"}], "identity field 'slug'"),
        ([party("a"), {"slug": None, "title": "x"}], "identity field 'slug'"),
        ([party("a", price="free")], "field 'price' is invalid"),
        ([{"slug": "a"}], "field 'title' is invalid"),
        ([{**party("a"), "secret_key": "x"}], "does not declare"),
        ([party("a"), party("a")], "repeats"),
        ([], "must not be empty"),
        ({"slug": "a"}, "list of dicts"),
        (["slug"], "must be a dict"),
    ],
)
def test_invalid_records_raise_typed_errors_and_write_nothing(job, records, match):
    with pytest.raises(ConfigurationError, match=match) as error:
        rastro.load_records(job, records, source_url=SOURCE)
    assert "secret_key" not in str(error.value)
    assert not (job.parent / "parties.sqlite").exists()


def test_a_failed_second_load_keeps_the_first_snapshot(job):
    rastro.load_records(job, [party("a"), party("b")], source_url=SOURCE)
    with pytest.raises(ConfigurationError):
        rastro.load_records(job, [party("a"), {"title": "no slug"}], source_url=SOURCE)
    assert set(stored(job)) == {"a", "b"}


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"source_url": "https://parties.example/x?id=1"}, "query"),
        ({"source_url": "ftp://parties.example/x"}, "source_url"),
        ({"source_url": SOURCE, "observed_at": datetime(2026, 1, 1)}, "timezone-aware"),
        ({"source_url": SOURCE, "observed_at": datetime.now(UTC) + timedelta(days=1)}, "future"),
    ],
)
def test_invalid_source_or_time_is_rejected(job, options, match):
    with pytest.raises(ConfigurationError, match=match):
        rastro.load_records(job, [party("a")], **options)


def test_record_count_is_bounded_by_the_job_limits(job):
    job.write_text(JOB.replace("[dataset]", "[limits]\nmax_records = 2\n[dataset]"), "utf-8")
    with pytest.raises(ResourceLimitError):
        rastro.load_records(job, [party(str(i)) for i in range(3)], source_url=SOURCE)


def test_a_job_without_a_dataset_is_rejected(tmp_path):
    path = tmp_path / "job.toml"
    path.write_text(JOB.split("[dataset]")[0], encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"\[dataset\]"):
        rastro.load_records(path, [party("a")], source_url=SOURCE)


def test_a_declared_consistency_gate_keeps_the_last_valid_snapshot(job):
    """N15, QUAL-005, SYNC-008: the job's [[consistency]] rules apply as in ``rastro job load``."""
    rule = '\n[[consistency]]\nkind = "distinct_ratio"\nfield = "title"\nmin_ratio = 1.0\n'
    job.write_text(JOB + rule + "min_records = 2\n", encoding="utf-8")
    first = rastro.load_records(job, [party("a", "One"), party("b", "Two")], source_url=SOURCE)
    assert first.status == "success"
    repeated = [party("a", "Repeated"), party("b", "Repeated")]
    result = rastro.load_records(job, repeated, source_url=SOURCE)
    assert result.status != "success"
    assert {r["title"] for r in stored(job).values()} == {"One", "Two"}


@pytest.mark.parametrize("record", [party("\ud800"), party("a", title="\ud800")])
def test_an_unencodable_value_is_a_safe_configuration_error(job, record):
    """N15, SEC-005: a lone surrogate fails typed, is not echoed, and writes nothing."""
    with pytest.raises(ConfigurationError, match="not valid text") as error:
        rastro.load_records(job, [record], source_url=SOURCE)
    assert "\ud800" not in str(error.value)
    assert "ud800" not in str(error.value)
    assert not (job.parent / "parties.sqlite").exists()
