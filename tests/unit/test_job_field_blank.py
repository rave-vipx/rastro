"""QUAL-002 / QUAL-004 / JOB-001: blank job values are missing, never successful empty output."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rastro.domain.job_fields import (
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    IntegerField,
    NumberField,
    StringField,
    UrlField,
    build_job_schema,
)
from rastro.domain.models import schema_fingerprint

BLANKS = ["", " ", "\xa0", " \t\n\xa0 "]


def _validate(field, value, **context):
    model = build_job_schema([field])
    return getattr(model.model_validate({field.name: value}, context=context), field.name)


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize(
    "field",
    [
        StringField(name="v"),
        StringField(name="v", min_length=1),
        IntegerField(name="v"),
        NumberField(name="v"),
        DecimalField(name="v"),
        DateField(name="v"),
        DateField(name="v", localized=True),
        DateTimeField(name="v"),
        UrlField(name="v"),
    ],
)
def test_required_blank_values_are_validation_errors(field, blank):
    with pytest.raises(ValidationError):
        _validate(field, blank, source_url="https://acme.example/careers")


@pytest.mark.parametrize("blank", BLANKS)
def test_optional_blank_values_stay_present_and_meet_constraints(blank):
    assert _validate(StringField(name="v", required=False), blank) == ""
    with pytest.raises(ValidationError):
        _validate(StringField(name="v", required=False, min_length=1), blank)
    with pytest.raises(ValidationError):
        _validate(IntegerField(name="v", required=False), blank)


def test_surrounding_whitespace_is_stripped_before_constraints():
    assert _validate(StringField(name="v"), "\xa0 Engineer \n") == "Engineer"
    with pytest.raises(ValidationError):
        _validate(StringField(name="v", min_length=3), " ab\xa0")
    assert _validate(StringField(name="v", pattern=r"^\d+$"), " 42 ") == "42"


def test_numbers_dates_and_urls_accept_surrounding_whitespace():
    assert _validate(IntegerField(name="v"), " 5\xa0") == 5
    assert _validate(DecimalField(name="v"), "\xa012.50 ") == Decimal("12.50")
    assert _validate(DateField(name="v"), " 2026-01-02 ") == date(2026, 1, 2)
    url = _validate(UrlField(name="v"), " /jobs/101 ", source_url="https://acme.example/careers")
    assert str(url) == "https://acme.example/jobs/101"


def test_inner_whitespace_and_non_strings_are_untouched():
    assert _validate(StringField(name="v"), "a  b") == "a  b"
    assert _validate(BooleanField(name="v"), True) is True
    with pytest.raises(ValidationError):
        _validate(UrlField(name="v"), "/jobs/1 01", source_url="https://acme.example/")


def test_blank_handling_keeps_schema_fingerprints():
    fields = [
        StringField(name="s", min_length=1),
        StringField(name="o", required=False),
        IntegerField(name="i"),
        NumberField(name="n", required=False),
        BooleanField(name="b"),
        DecimalField(name="d"),
        DateField(name="dt", localized=True),
        DateTimeField(name="dtt"),
        UrlField(name="u"),
    ]
    # Pinned before this change: recipe memory keys must not move.
    assert schema_fingerprint(build_job_schema(fields)) == (
        "91ce72ba9c61131e4d53dd157ddb85e5a7983c54d86ea6da296497833b140003"
    )


def test_job_run_with_blank_required_text_is_not_success(tmp_path, capsys):
    from rastro.cli import main

    (tmp_path / "page.html").write_text("<ul><li><h3>&nbsp;</h3></li></ul>", encoding="utf-8")
    job = tmp_path / "job.toml"
    job.write_text(
        'format_version = 1\n[recipe]\nstrategy = "css"\nrecord_selector = "li"\n'
        'fields = { t = "h3::text" }\n[[fields]]\nname = "t"\ntype = "string"\n'
        '[[sources]]\npath = "page.html"\n',
        encoding="utf-8",
    )
    assert main(["job", "run", str(job)]) != 0
    assert '"status": "success"' not in capsys.readouterr().out
