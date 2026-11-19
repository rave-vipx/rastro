"""rastro.read_records: records read back from a job's dataset, and nested CSV export.

Synthetic data only. Records are written through ``rastro.load_records``.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import rastro
from rastro import ConfigurationError
from rastro.cli import main
from rastro.read_api import DatasetRead, read_records

LISTING = "https://parties.example/listing"
OTHER = "https://tickets.example/listing"
JOB = """
format_version = 1
[recipe]
strategy = "css"
record_selector = ".party"
[recipe.fields]
slug = ".slug::text"
title = "h2::text"
[[fields]]
name = "slug"
type = "string"
[[fields]]
name = "title"
type = "string"
[[fields]]
name = "tickets"
type = "list"
required = false
[fields.item]
name = "ticket"
type = "object"
[[fields.item.members]]
name = "tier"
type = "string"
[[fields.item.members]]
name = "price"
type = "decimal"
[[sources]]
url = "https://parties.example/listing"
[acquisition]
allowed_hosts = ["parties.example", "tickets.example"]
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


def party(slug: str, **extra: object) -> dict[str, object]:
    return {"slug": slug, "title": f"Noche {slug}", **extra}


def test_read_records_since_timedelta(job):
    old = datetime.now(UTC) - timedelta(hours=2)
    rastro.load_records(job, [party("old")], source_url=OTHER, observed_at=old)
    rastro.load_records(job, [party("new")], source_url=LISTING)
    recent = read_records(job, since=timedelta(minutes=30))
    assert [r.fields["slug"] for r in recent.records] == ["new"]
    assert len(read_records(job, since=datetime.now(UTC) - timedelta(hours=3)).records) == 2
    assert len(read_records(job).records) == 2


def test_read_records_fields_projection(job):
    rastro.load_records(job, [party("a")], source_url=LISTING)
    (record,) = read_records(job, fields=["slug"]).records
    assert set(record.fields) == {"slug"}
    assert set(record.provenance) == {"slug"}
    with pytest.raises(ConfigurationError, match="not declared"):
        read_records(job, fields=["nope"])
    with pytest.raises(ConfigurationError, match="dataset"):
        read_records(job, dataset="other")


def test_multi_origin_merged_with_provenance(job):
    rastro.load_records(job, [party("a")], source_url=LISTING)
    rastro.load_records(job, [party("a")], source_url=OTHER)
    (record,) = read_records(job).records
    assert record.origins == (LISTING, OTHER)
    assert record.provenance["slug"].source_url in record.origins
    assert record.first_observed_at <= record.last_observed_at
    assert record.uncertain_match is False


def test_read_records_flags_untrusted(job):
    rastro.load_records(job, [party("a")], source_url=LISTING)
    result = read_records(job)
    assert isinstance(result, DatasetRead)
    assert (result.schema_version, result.data_trust) == (1, "untrusted_source_data")
    assert result.dataset == "parties"
    assert "data_trust" in result.model_dump(mode="json")


def test_nested_values_are_validated_and_kept_native(job):
    tickets = [{"tier": "general", "price": "450.00"}, {"tier": "vip", "price": "900"}]
    rastro.load_records(job, [party("a", tickets=tickets)], source_url=LISTING)
    (record,) = read_records(job).records
    assert [t["tier"] for t in record.fields["tickets"]] == ["general", "vip"]
    with pytest.raises(ConfigurationError, match="tickets"):
        rastro.load_records(
            job, [party("b", tickets=[{"tier": "x", "price": "free"}])], source_url=LISTING
        )


def test_csv_flattens_nested_documented(job, capsys):
    tickets = [{"tier": "general", "price": "450.00"}, {"tier": "vip", "price": "900"}]
    rastro.load_records(job, [party("a", tickets=tickets)], source_url=LISTING)
    assert main(["dataset", "export", str(job.parent / "parties.sqlite"), "--format", "csv"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows[0]["tickets[0].price"] == "450.00"
    assert rows[0]["tickets[1].tier"] == "vip"
    assert rows[0]["slug"] == "a"
    assert main(["dataset", "export", str(job.parent / "parties.sqlite")]) == 0
    assert '"tier": "general"' in capsys.readouterr().out  # JSON stays native
