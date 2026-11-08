import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from rastro import (
    ConfigurationError,
    DecimalField,
    FileJobSource,
    IntegerField,
    Job,
    JobSummary,
    NumberField,
    StringField,
    build_job_schema,
    job_json_schema,
    job_run_json_schema,
    schema_fingerprint,
)
from rastro.cli import main

SCHEMAS_DIR = Path(__file__).parents[2] / "docs" / "schemas"


def test_all_field_types_and_deterministic_schema_fingerprint():
    fields_data = [
        {"name": "title", "type": "string", "min_length": 1, "max_length": 80, "pattern": r"^\S+"},
        {"name": "stock", "type": "integer", "ge": 0, "le": 999},
        {"name": "rating", "type": "number", "required": False, "ge": 0.0, "le": 5.0},
        {"name": "available", "type": "boolean"},
        {"name": "price", "type": "decimal", "ge": "0.01", "le": "9999.99"},
        {"name": "released", "type": "date", "required": False},
        {"name": "updated_at", "type": "datetime", "required": False},
        {"name": "homepage", "type": "url", "required": False},
    ]
    job_a = Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"path": "a.html"}],
            "discover_recipes": True,
            "fields": fields_data,
        }
    )
    job_b = Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"path": "a.html"}],
            "discover_recipes": True,
            "fields": list(reversed(fields_data)),
        }
    )
    model_a, model_b = job_a.build_schema(), job_b.build_schema()
    assert schema_fingerprint(model_a) == schema_fingerprint(model_b)
    record = model_a.model_validate(
        {
            "title": "Notebook",
            "stock": "12",
            "rating": "4.5",
            "available": "true",
            "price": "19.95",
            "released": "2026-10-30",
            "updated_at": "2026-10-30T12:00:00Z",
            "homepage": "https://example.com/item",
        }
    )
    assert record.price == Decimal("19.95") and record.released == date(2026, 10, 30)
    assert isinstance(record.updated_at, datetime)
    for nonfinite in ({"rating": "NaN"}, {"price": "Infinity"}):
        with pytest.raises(ValidationError):
            model_a.model_validate({**record.model_dump(), **nonfinite})


def test_build_job_schema_unconstrained_and_invalid_collections():
    with pytest.raises(ConfigurationError):
        build_job_schema([])
    with pytest.raises(ConfigurationError):
        build_job_schema([StringField(name="x"), StringField(name="x")])
    plain_model = build_job_schema(
        [IntegerField(name="i"), NumberField(name="n"), DecimalField(name="d")]
    )
    assert set(plain_model.model_fields) == {"d", "i", "n"}


@pytest.mark.parametrize(
    "field_spec",
    [
        {"name": "_bad", "type": "string"},
        {"name": "for", "type": "string"},
        {"name": "model_config", "type": "string"},
        {"name": "schema", "type": "string"},
        {"name": "x", "type": "string", "ge": 0},
        {"name": "x", "type": "string", "min_length": 5, "max_length": 2},
        {"name": "x", "type": "string", "pattern": "[unclosed"},
        {"name": "x", "type": "integer", "ge": 10, "le": 2},
        {"name": "x", "type": "number", "ge": True},
        {"name": "x", "type": "number", "ge": float("nan")},
        {"name": "x", "type": "number", "ge": 5.0, "le": 1.0},
        {"name": "x", "type": "decimal", "ge": "10", "le": "2"},
    ],
)
def test_field_contract_rejects_invalid_names_and_constraints(field_spec):
    with pytest.raises(ValidationError):
        Job.model_validate(
            {
                "format_version": 1,
                "sources": [{"path": "a.html"}],
                "discover_recipes": True,
                "fields": [field_spec],
            }
        )


def test_file_source_urls_summary_and_published_schemas_match(capsys):
    assert FileJobSource(path="sub/items.html").resolved_source_url() == (
        "https://local.invalid/sub/items.html"
    )
    explicit = FileJobSource(path="sub/items.html", url="https://example.com/items")
    assert explicit.resolved_source_url() == "https://example.com/items"
    absolute = FileJobSource(path="/Users/private_owner/data/items.html")
    assert "private_owner" not in absolute.resolved_source_url()
    assert absolute.resolved_source_url().startswith("https://local.invalid/file/")

    with pytest.raises(ValidationError):
        JobSummary(
            schema_fingerprint="a" * 64,
            source_count=2,
            succeeded=1,
            partial=0,
            failed=0,
            records_accepted=1,
            records_rejected=0,
            status="success",
        )

    assert json.loads((SCHEMAS_DIR / "job.schema.json").read_text()) == job_json_schema()
    assert json.loads((SCHEMAS_DIR / "job-run.schema.json").read_text()) == job_run_json_schema()
    assert main(["job", "schema"]) == 0
    assert json.loads(capsys.readouterr().out) == job_json_schema()
    assert main(["job", "schema", "--target", "run"]) == 0
    assert json.loads(capsys.readouterr().out) == job_run_json_schema()


def test_localized_datetime_field_accepts_spanish_and_iso():
    from zoneinfo import ZoneInfo

    from rastro import DateField, DateTimeField

    schema = build_job_schema(
        [DateTimeField(name="starts_at", localized=True), DateField(name="day", localized=True)]
    )
    context = {
        "timezone": "America/Montevideo",
        "observed_at": datetime(2026, 11, 7, tzinfo=ZoneInfo("America/Montevideo")),
    }
    for value in ("22 NOV 2026 - 19 H", "2026-11-22T19:00:00-03:00"):
        record = schema.model_validate(
            {"starts_at": value, "day": "06 de noviembre"}, context=context
        )
        assert record.starts_at.isoformat() == "2026-11-22T19:00:00-03:00"
        assert record.day == date(2026, 11, 6)
    with pytest.raises(ValidationError, match="unparseable localized date"):
        schema.model_validate(
            {"starts_at": "06 de noviembre", "day": "06 de noviembre"}, context=context
        )


def test_non_localized_field_unchanged():
    from rastro import DateField, DateTimeField, UrlField

    schema = build_job_schema(
        [DateField(name="day"), DateTimeField(name="starts_at"), UrlField(name="url")]
    )
    assert (
        schema_fingerprint(schema)
        == "941dfb4501ad701cb722808fb8b29fd74035f575db2319caab8fed98026d966f"
    )
    with pytest.raises(ValidationError):
        schema.model_validate(
            {
                "day": "06 de noviembre",
                "starts_at": "22 NOV 2026 - 19 H",
                "url": "https://example.com",
            }
        )


def test_relative_url_resolved_against_source_and_scheme_checked():
    from rastro import UrlField

    schema = build_job_schema([UrlField(name="url")])
    context = {"source_url": "https://example.com/shows/"}
    assert (
        str(schema.model_validate({"url": "/shows/item/1"}, context=context).url)
        == "https://example.com/shows/item/1"
    )
    for value in ("javascript:x", "ftp://example.com/file"):
        with pytest.raises(ValidationError):
            schema.model_validate({"url": value}, context=context)
    with pytest.raises(ValidationError):
        schema.model_validate({"url": "/shows/item/1"})


def test_localized_date_preserves_iso_midnight_and_missing_context_fails():
    from rastro import DateField

    schema = build_job_schema([DateField(name="day", localized=True)])
    assert schema.model_validate({"day": "2026-11-22T00:00:00Z"}).day == date(2026, 11, 22)
    with pytest.raises(ValidationError, match="unparseable localized date"):
        schema.model_validate({"day": "22 nov"})


@pytest.mark.parametrize(
    "value", ["", "   ", "Entradas agotadas", "N/A", "12345", "//evil.example/phish"]
)
def test_url_with_source_context_rejects_non_urls(value):
    from rastro import UrlField

    schema = build_job_schema([UrlField(name="url")])
    with pytest.raises(ValidationError):
        schema.model_validate({"url": value}, context={"source_url": "https://example.com/shows/"})


def test_localized_datetime_requires_an_iso_time():
    from rastro import DateTimeField

    schema = build_job_schema([DateTimeField(name="starts_at", localized=True)])
    with pytest.raises(ValidationError):
        schema.model_validate({"starts_at": "2026-11-22"})


def test_localized_date_converts_space_separated_iso_datetime_to_region():
    from rastro import DateField

    schema = build_job_schema([DateField(name="day", localized=True)])
    record = schema.model_validate(
        {"day": "2026-11-21 01:30:00Z"}, context={"timezone": "America/Montevideo"}
    )
    assert record.day == date(2026, 11, 20)


@pytest.mark.parametrize("value", ["06 de noviembre", "2026-11-21T01:30:00Z"])
def test_unknown_timezone_is_a_validation_error(value):
    from rastro import DateField

    schema = build_job_schema([DateField(name="day", localized=True)])
    with pytest.raises(ValidationError):
        schema.model_validate(
            {"day": value},
            context={"timezone": "Invalid/Timezone", "observed_at": datetime(2026, 11, 8)},
        )
