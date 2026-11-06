"""`[dataset]` job block contract (DATA-002, JOB-002)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from rastro import Job, job_json_schema

BASE: dict[str, Any] = {
    "format_version": 1,
    "recipe": {"strategy": "css", "record_selector": "li", "fields": {"title": "b::text"}},
    "sources": [{"path": "page.html"}],
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "venue", "type": "string"},
        {"name": "city", "type": "string", "required": False},
    ],
}
DATASET: dict[str, Any] = {
    "name": "uy_dance_agenda",
    "path": "agenda.sqlite",
    "identity": ["title", "venue"],
}


def _job(**dataset: Any) -> Job:
    return Job.model_validate({**BASE, "dataset": {**DATASET, **dataset}})


def test_dataset_block_defaults_and_effective_approve():
    job = _job()
    assert job.dataset is not None
    assert job.dataset.approve is None
    assert job.dataset.approved_fields(["title", "venue", "city"]) == ("title", "venue", "city")
    narrowed = _job(approve=["venue", "title"]).dataset
    assert narrowed is not None
    assert narrowed.approved_fields(["title", "venue", "city"]) == ("venue", "title")
    assert _job(match_tolerance_minutes=90).dataset.match_tolerance_minutes == 90  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "override",
    [
        {"identity": ["title", "nope"]},
        {"identity": ["title"], "approve": ["venue"]},
        {"approve": ["title", "nope"]},
        {"identity": []},
        {"identity": ["title"] * 2},
        {"identity": [f"f{i}" for i in range(9)]},
        {"name": "Uy-Dance"},
        {"name": "1abc"},
        {"name": "a" * 65},
        {"path": ":memory:"},
        {"path": "~/agenda.sqlite"},
        {"path": "file:agenda.sqlite"},
        {"path": "../agenda.sqlite"},
        {"match_tolerance_minutes": 0},
        {"match_tolerance_minutes": "90"},
        {"unknown_key": 1},
    ],
)
def test_dataset_block_validation(override: dict[str, Any]):
    with pytest.raises(ValidationError):
        _job(**override)


def test_dataset_validation_names_the_reason():
    with pytest.raises(ValidationError, match="identity must be listed in approve"):
        _job(identity=["title"], approve=["venue"])
    with pytest.raises(ValidationError, match="declared fields"):
        _job(identity=["title", "nope"])


def test_job_without_dataset_block_is_unchanged():
    job = Job.model_validate(BASE)
    assert job.dataset is None
    assert "dataset" not in job.redacted_dump()
    schema = job_json_schema()
    assert "dataset" not in schema["required"]
    assert json.loads(json.dumps(schema)) == schema
