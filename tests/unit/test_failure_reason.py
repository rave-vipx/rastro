"""Content-free failure detail tells robots, 403, 429 and timeouts apart (DATA-005, SCR-002)."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro.application.outcomes import failure_result
from rastro.domain.failure_reason import failure_detail
from rastro.domain.models import Issue, Outcome, Source


class Item(BaseModel):
    name: str


def result(status, code, http_status=None):
    failed = failure_result(
        status=status,
        issue_code=code,
        source=Source(url="https://example.org/list?token=secret"),
        schema=Item,
        started_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    return failed.model_copy(update={"http_status": http_status})


@pytest.mark.parametrize(
    ("status", "code", "http_status", "expected"),
    [
        (Outcome.ACCESS_RESTRICTION, "robots_denied", None, "robots_denied"),
        (Outcome.ACCESS_RESTRICTION, "rate_limited", 429, "http_429"),
        (Outcome.ACCESS_RESTRICTION, "access_restricted", 403, "http_403"),
        (Outcome.ACCESS_RESTRICTION, "access_restricted", 401, "http_401"),
        (Outcome.TRANSIENT_FAILURE, "request_timeout", None, "timeout"),
        (Outcome.TRANSIENT_FAILURE, "operation_timeout", None, "timeout"),
        (Outcome.TRANSIENT_FAILURE, "http_transient_failure", 503, "http_transient_failure"),
        (Outcome.MISSING_DATA, "Not A Code <b>", None, None),
    ],
)
def test_failure_detail_codes(status, code, http_status, expected):
    assert failure_detail(result(status, code, http_status)) == expected


def test_successful_results_have_no_detail():
    ok = result(Outcome.MISSING_DATA, "x").model_copy(
        update={"status": Outcome.SUCCESS, "issues": (Issue(code="robots_denied"),)}
    )
    assert failure_detail(ok) is None
