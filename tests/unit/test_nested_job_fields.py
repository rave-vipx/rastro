"""Bounded field structures, placeholder diagnostics and regional datetimes."""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from rastro.domain.job_fields import (
    DateField,
    DateTimeField,
    JobField,
    StringField,
    build_job_schema,
)
from rastro.domain.sources import schema_fingerprint


def field(data):
    return TypeAdapter(JobField).validate_python(data)


def tickets(**options):
    return field(
        {
            "name": "tickets",
            "type": "list",
            "item": {
                "name": "item",
                "type": "object",
                "members": [
                    {"name": "tier", "type": "string"},
                    {"name": "price", "type": "decimal"},
                ],
            },
            **options,
        }
    )


def test_list_of_objects_builds_model():
    schema = build_job_schema([tickets()])
    result = schema.model_validate({"tickets": [{"tier": "A", "price": "10.5"}]})
    assert result.tickets[0].tier == "A"
    assert result.tickets[0].price == Decimal("10.5")
    assert schema.model_json_schema()["properties"]["tickets"]["maxItems"] == 100


def test_object_in_object_refused():
    valid = tickets().item.model_dump()
    assert field(valid).type == "object"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        field({**valid, "members": [valid]})


def test_list_in_list_refused():
    valid = tickets().model_dump()
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        field({**valid, "item": valid})


@pytest.mark.parametrize("required", [True, False])
def test_null_values_raise_null_placeholder(required):
    schema = build_job_schema(
        [
            DateField(
                name="day",
                required=required,
                null_values=("0000-00-00",),
            )
        ]
    )
    with pytest.raises(ValidationError) as caught:
        schema.model_validate({"day": " 0000-00-00 "})
    assert caught.value.errors()[0]["type"] == "null_placeholder"


def test_placeholders_casefold_without_leaking_value():
    schema = build_job_schema([StringField(name="title", null_values=(" N/A ",))])
    with pytest.raises(ValidationError) as caught:
        schema.model_validate({"title": " n/a "})
    assert caught.value.errors(include_input=False)[0]["msg"] == "Value is a null placeholder"


@pytest.mark.parametrize("localized", [False, True])
@pytest.mark.parametrize("value", ["2026-05-01T20:00", datetime(2026, 5, 1, 20)])
def test_naive_datetime_takes_region_timezone(localized, value):
    schema = build_job_schema([DateTimeField(name="at", localized=localized)])
    context = {"timezone": "America/Argentina/Buenos_Aires", "field_issues": []}
    result = schema.model_validate({"at": value}, context=context)
    assert result.at.isoformat() == "2026-05-01T20:00:00-03:00"
    assert [(i.code, i.field) for i in context["field_issues"]] == [
        ("naive_datetime_localized", "at")
    ]


def test_schema_fingerprint_stable_for_scalar_jobs():
    from rastro.domain.job_fields import UrlField

    schema = build_job_schema(
        [DateField(name="day"), DateTimeField(name="starts_at"), UrlField(name="url")]
    )
    assert (
        schema_fingerprint(schema)
        == "941dfb4501ad701cb722808fb8b29fd74035f575db2319caab8fed98026d966f"
    )


def test_list_item_drop_appends_context_issue():
    schema = build_job_schema([tickets()])
    context = {"field_issues": []}
    result = schema.model_validate(
        {
            "tickets": [
                {"tier": "A", "price": "10.5"},
                {"tier": "B", "price": "secret"},
            ]
        },
        context=context,
    )
    assert len(result.tickets) == 1
    assert [(i.code, i.field) for i in context["field_issues"]] == [
        ("list_item_dropped", "tickets")
    ]
    assert "secret" not in repr(context)


def test_optional_member_failure_becomes_null():
    spec = field(
        {
            "name": "ticket",
            "type": "object",
            "members": [
                {"name": "tier", "type": "string"},
                {"name": "price", "type": "decimal", "required": False},
            ],
        }
    )
    context = {"field_issues": []}
    result = build_job_schema([spec]).model_validate(
        {"ticket": {"tier": "A", "price": "bad"}}, context=context
    )
    assert result.ticket.price is None
    assert [(i.code, i.field) for i in context["field_issues"]] == [("member_nulled", "price")]


@pytest.mark.parametrize(
    "options", [{"min_items": 3, "max_items": 2}, {"max_items": 1001}, {"min_items": True}]
)
def test_list_limits_refused(options):
    with pytest.raises(ValidationError):
        tickets(**options)


@pytest.mark.parametrize("values", [(), ("",), ("a" * 65,), tuple(str(i) for i in range(17))])
def test_placeholder_bounds(values):
    if not values:
        assert StringField(name="title", null_values=values).null_values == ()
    else:
        with pytest.raises(ValidationError):
            StringField(name="title", null_values=values)


def test_list_minimum_checked_after_drops():
    with pytest.raises(ValidationError):
        build_job_schema([tickets(min_items=1)]).model_validate(
            {"tickets": [{"tier": "A", "price": "bad"}]},
            context={"field_issues": []},
        )


def test_object_member_names_unique():
    with pytest.raises(ValidationError):
        field(
            {
                "name": "ticket",
                "type": "object",
                "members": [
                    {"name": "tier", "type": "string"},
                    {"name": "tier", "type": "integer"},
                ],
            }
        )
