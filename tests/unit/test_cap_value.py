"""The reached cap of a result and its configured value."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro.application.outcomes import failure_result
from rastro.application.run_metrics import cap_value
from rastro.domain.jobs import Job
from rastro.domain.metrics import RunMetric, reached_cap
from rastro.domain.models import Issue, Outcome, Source


class Item(BaseModel):
    name: str


def result(stop=None, codes=()):
    base = failure_result(
        status=Outcome.PARTIAL_SUCCESS,
        issue_code="placeholder",
        source=Source(url="https://agenda.example/one"),
        schema=Item,
        started_at=datetime(2026, 11, 17, tzinfo=UTC),
    )
    issues = tuple(Issue(code=c) for c in codes)
    return base.model_copy(update={"pagination_stop": stop, "issues": issues})


JOB = Job.model_validate(
    {
        "format_version": 1,
        "sources": [{"url": "https://agenda.example/one"}],
        "fields": [{"name": "name", "type": "string"}],
        "recipe": {
            "strategy": "css",
            "record_selector": "article",
            "fields": {"name": "h2::text"},
            "pagination": {"next": "a.next::attr(href)", "max_pages": 4},
        },
        "acquisition": {"allowed_hosts": ["agenda.example"], "max_requests": 12},
        "limits": {"max_records": 300},
        "scroll": {"max_steps": 7},
        "spider": {"mode": "spider", "max_depth": 3, "max_pages": 40, "links": "a::attr(href)"},
    }
)


@pytest.mark.parametrize(
    ("stop", "codes", "cap", "value"),
    [
        ("spider_page_limit", ("pagination_max_pages",), "spider_page_limit", 40),
        ("spider_depth_limit", (), "spider_depth_limit", 3),
        ("spider_budget", (), "spider_budget", 12),
        ("budget", (), "pagination_budget", 12),
        ("record_limit", (), "pagination_record_limit", 300),
        ("max_pages", (), "pagination_max_pages", 4),
        (None, ("scroll_max_steps",), "scroll_max_steps", 7),
        ("no_next", ("scroll_budget",), "scroll_budget", 12),
        ("no_next", ("scroll_ignored_http",), None, None),
        ("spider_complete", (), None, None),
    ],
)
def test_the_run_stop_wins_and_values_come_from_the_job(stop, codes, cap, value):
    found = result(stop, codes)
    assert reached_cap(found) == cap
    assert cap_value(JOB, found) == value
    assert cap_value(None, found) is None


def test_unknown_cap_codes_are_refused_in_rows():
    with pytest.raises(ValueError, match="unknown cap code"):
        RunMetric(
            run_id="r",
            source_url="https://a.example/",
            host="a.example",
            strategy="explicit",
            started_at=datetime(2026, 11, 17, tzinfo=UTC),
            finished_at=datetime(2026, 11, 17, tzinfo=UTC),
            outcome="success",
            cap="<b>not a cap</b>",
        )
