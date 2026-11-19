"""One bad value, record or stage costs at most that value or record, never the source."""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError, PydanticSerializationError

from rastro import Issue, Outcome, Rastro, Recipe, Source
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.dataset_load import load_results
from rastro.application.field_isolation import count_nulled, validate_isolated
from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job
from rastro.domain.source_notes import SourceNotes
from rastro.ports.parser import Candidate, ParsedDocument


@pytest.fixture
def sample(tmp_path):
    shutil.copytree(Path("examples/event-agenda/sample"), tmp_path / "sample")
    return tmp_path / "sample"


class Item(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal | None = None
    rating: int | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def placeholder(cls, value):
        if value == "N/A":
            raise PydanticCustomError("null_placeholder", "placeholder")
        return value


class FakeParser:
    def __init__(self, *candidates: Candidate, issues=()) -> None:
        self.candidates = candidates
        self.issues = issues

    def parse(self, document, recipe, limits):
        return ParsedDocument(candidates=self.candidates, issues=tuple(self.issues))


def candidate(**values) -> Candidate:
    return Candidate(values, {name: f"/{name}" for name in values})


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(strategy="jsonld", item_type="Item", fields={"name": "/name"})


@pytest.fixture
def source() -> Source:
    return Source(url="https://shop.example/items")


def run(source, recipe, *candidates, schema=Item, issues=()):
    parser = FakeParser(*candidates, issues=issues)
    return Rastro(parser=parser).extract_html("x", source=source, schema=schema, recipe=recipe)


def test_optional_field_bad_value_nulls_with_issue(source, recipe):
    result = run(source, recipe, candidate(name="Pen", price="abc"))
    assert result.status == Outcome.PARTIAL_SUCCESS
    record = result.records[0]
    assert record.data.price is None
    assert "price" not in record.evidence
    [issue] = result.issues
    assert (issue.code, issue.field, issue.record_index) == ("field_nulled", "price", 0)
    assert "abc" not in result.model_dump_json()
    assert result.metrics.rejected == 0


def test_required_field_bad_value_rejects_record_only(source, recipe):
    result = run(
        source,
        recipe,
        candidate(name="A"),
        candidate(name=""),
        candidate(name="C", price="3"),
    )
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [r.data.name for r in result.records] == ["A", "C"]
    assert result.metrics.rejected == 1
    [issue] = result.issues
    assert (issue.code, issue.field, issue.record_index) == ("validation_error", "name", 1)


def test_null_placeholder_reason_reported(source, recipe):
    result = run(source, recipe, candidate(name="Pen", rating="N/A"))
    assert result.records[0].data.rating is None
    assert [(i.code, i.field) for i in result.issues] == [
        ("null_placeholder", "rating"),
        ("field_nulled", "rating"),
    ]


def test_notes_fields_nulled_counts(source, recipe):
    result = run(
        source,
        recipe,
        candidate(name="A", price="x", rating="N/A"),
        candidate(name="B", price="y"),
    )
    counts = count_nulled(result.issues)
    assert counts == {"price": 2, "rating": 1}
    assert SourceNotes(fields_nulled=counts).model_dump()["fields_nulled"] == counts
    assert count_nulled(()) == {}


def test_model_level_failure_rejects_the_record():
    outcome = validate_isolated(Item, {"price": "1"}, context={}, record_index=4)
    assert outcome.data is None
    assert outcome.issues[0].record_index == 4
    assert outcome.nulled == ()


@pytest.mark.parametrize("stage", ["decode", "parse", "validate", "serialize", "evidence"])
def test_fault_injection_per_stage(stage, source, recipe):
    bad = {
        "decode": Candidate({"name": "x"}, {"name": "/x"}, issues=(Issue(code="invalid_json"),)),
        "parse": Candidate({}, {}),
        "validate": candidate(name=""),
        "serialize": candidate(name="\ud800"),
        "evidence": Candidate({"name": "x"}, {"name": 123}),
    }[stage]
    result = run(source, recipe, candidate(name="good"), bad)
    assert [r.data.name for r in result.records] == ["good"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.metrics.accepted == result.metrics.rejected == 1


def test_dedup_stage_keeps_every_record(source, recipe):
    result = run(source, recipe, candidate(name="same"), candidate(name="same"))
    assert len(result.records) == 2


def test_storage_stage_drops_one_record_not_the_source(sample, monkeypatch):
    job = load_job(sample / "job.toml")
    report = run_job(job, base_dir=sample)
    data_type = type(report.results[0].records[0].data)
    victim = report.results[0].records[0].data
    original = data_type.model_dump

    def dump(self, *args, **kwargs):
        if self is victim:
            raise PydanticSerializationError("unserializable")
        return original(self, *args, **kwargs)

    baseline = SQLiteDatasetStore(sample / "base.sqlite")
    full = load_results(job.dataset, [f.name for f in job.fields], report, baseline)
    monkeypatch.setattr(data_type, "model_dump", dump)
    store = SQLiteDatasetStore(sample / "agenda.sqlite")
    loaded = load_results(job.dataset, [f.name for f in job.fields], report, store)
    source = loaded.sources[0]
    assert [(i.code, i.record_index) for i in source.issues] == [("invalid_record", 0)]
    assert sum(source.counts.values()) == sum(full.sources[0].counts.values()) - 1
    assert loaded.status != "storage_error"
