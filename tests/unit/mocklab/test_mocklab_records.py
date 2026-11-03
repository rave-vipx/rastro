import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import BaseModel, Field, HttpUrl, model_validator

from rastro.domain.job_fields import StringField, build_job_schema
from rastro.mocklab import MockLabError
from rastro.mocklab.records import synthesize_records


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Wide(BaseModel):
    title: str = Field(min_length=3, max_length=5)
    count: int = Field(gt=10, lt=13)
    ratio: float = Field(ge=0.5, le=2)
    price: Decimal = Field(ge=0)
    active: bool
    day: date
    moment: datetime
    link: HttpUrl
    kind: Literal["a", "b"]
    color: Color
    note: str | None = None


def test_generates_schema_valid_records_for_supported_types():
    out = synthesize_records(Wide, [], count=20, seed=5)
    assert len(out.records) == 20 and out.resampled_fields == ()
    for record in out.records:
        parsed = Wide.model_validate(record)
        assert 3 <= len(parsed.title) <= 5 and parsed.count in {11, 12}
        assert str(parsed.link).startswith("https://example.com/synthetic/")


def test_same_seed_is_reproducible_and_seeds_differ():
    first = synthesize_records(Wide, [], count=5, seed=1)
    assert first == synthesize_records(Wide, [], count=5, seed=1)
    assert first != synthesize_records(Wide, [], count=5, seed=2)


class Patterned(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    tags: list[str]


def test_unsatisfiable_fields_are_resampled_from_observed_values():
    observed = [{"currency": "USD", "tags": ["x"]}, {"currency": "EUR", "tags": []}]
    out = synthesize_records(Patterned, observed, count=4, seed=0)
    assert out.resampled_fields == ("currency", "tags")
    assert all(record["currency"] in {"USD", "EUR"} for record in out.records)


def test_fails_explicitly_without_observed_values():
    with pytest.raises(MockLabError, match="tags"):
        synthesize_records(Patterned, [], count=1, seed=0)
    with pytest.raises(MockLabError, match="currency"):
        synthesize_records(Patterned, [{"tags": []}], count=1, seed=0)


class Contradictory(BaseModel):
    a: int

    @model_validator(mode="after")
    def never(self):
        raise ValueError("never valid")


def test_model_level_failures_are_explicit():
    with pytest.raises(MockLabError):
        synthesize_records(Contradictory, [{"a": 1}], count=1, seed=0)


def test_supports_job_schemas():
    schema = build_job_schema([StringField(name="name", min_length=2, max_length=4)])
    out = synthesize_records(schema, [], count=3, seed=9)
    assert all(schema.model_validate(record) for record in out.records)
