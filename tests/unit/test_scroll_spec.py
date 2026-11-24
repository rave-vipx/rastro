"""The `[scroll]` job table is bounded, strict and optional."""

import pytest
from pydantic import ValidationError

from rastro.domain.jobs import Job, parse_job
from rastro.domain.scroll import ScrollSpec

BASE = """format_version = 1
[acquisition]
allowed_hosts = ["example.com"]
render = "browser"

[[sources]]
url = "https://example.com/"
[sources.recipe]
version = 1
strategy = "css"
record_selector = "p"
[sources.recipe.fields]
title = "::text"

[[fields]]
name = "title"
type = "string"
"""


def test_defaults_and_bounds():
    spec = ScrollSpec()
    assert (spec.max_steps, spec.settle_ms, spec.stop_when) == (10, 500, "no_new_records")
    assert ScrollSpec(max_steps=1, settle_ms=100).max_steps == 1
    assert ScrollSpec(max_steps=30, settle_ms=3000).settle_ms == 3000


@pytest.mark.parametrize(
    "bad",
    [
        {"max_steps": 0},
        {"max_steps": 31},
        {"max_steps": True},
        {"max_steps": "5"},
        {"settle_ms": 99},
        {"settle_ms": 3001},
        {"stop_when": "forever"},
        {"unknown": 1},
    ],
)
def test_rejects_out_of_bounds_values(bad):
    with pytest.raises(ValidationError):
        ScrollSpec(**bad)


def test_job_table_is_optional_and_parsed():
    assert parse_job(BASE, format="toml").scroll is None
    job = parse_job(BASE + "\n[scroll]\nmax_steps = 3\nsettle_ms = 200\n", format="toml")
    assert isinstance(job, Job) and job.scroll == ScrollSpec(max_steps=3, settle_ms=200)
    assert parse_job(BASE + "\n[scroll]\n", format="toml").scroll == ScrollSpec()


def test_job_table_rejects_a_bad_value_without_echoing_it():
    with pytest.raises(ValidationError) as error:
        parse_job(BASE + "\n[scroll]\nmax_steps = 31\n", format="toml")
    assert "31" not in str(error.value)


def test_absent_table_leaves_the_dump_unchanged():
    assert "scroll" not in parse_job(BASE, format="toml").model_dump(mode="json")
