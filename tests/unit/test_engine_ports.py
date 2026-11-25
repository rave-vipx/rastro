"""Engine strategy ports: engine-neutral shapes for adaptive, crawl and capture features."""

from collections.abc import Sequence

import pytest

from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.page_steps import PageStep
from rastro.ports.engine import (
    CapturedResponse,
    NetworkCapture,
    PageStepRunner,
    RecordCandidate,
)


def response(**changes: object) -> CapturedResponse:
    values: dict[str, object] = {
        "host": "shop.example",
        "path": "/api/items",
        "method": "GET",
        "status": 200,
        "media_type": "application/json",
        "body": b'{"items": []}',
    }
    return CapturedResponse(**(values | changes))  # type: ignore[arg-type]


def test_captured_responses_keep_no_query_and_hide_the_body_from_repr():
    captured = response(body=b"private-token-123")
    assert "private-token-123" not in repr(captured)
    for path in ("/api?token=1", "/api#x", "api"):
        with pytest.raises(ValueError, match="path"):
            response(path=path)
    with pytest.raises(ValueError, match="status"):
        response(status=42)


def test_record_candidates_are_immutable():
    candidate = RecordCandidate(record_selector="li.item", count=3, field_locators={"t": "h2"})
    with pytest.raises(TypeError):
        candidate.field_locators["t"] = "h3"  # type: ignore[index]
    with pytest.raises(ValueError, match="count"):
        RecordCandidate(record_selector="li", count=-1, field_locators={})


class _Capture:
    def captured(self) -> tuple[CapturedResponse, ...]:
        return (response(),)


class _Runner:
    async def run_steps(
        self, steps: Sequence[PageStep], *, policy: AcquisitionPolicy, max_bytes: int
    ) -> AcquisitionResult:
        raise NotImplementedError


def test_optional_session_features_are_detected_with_isinstance():
    assert isinstance(_Capture(), NetworkCapture)
    assert not isinstance(_Capture(), PageStepRunner)
    assert isinstance(_Runner(), PageStepRunner)
    assert not isinstance(object(), NetworkCapture)
