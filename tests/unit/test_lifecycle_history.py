from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from rastro import Source
from rastro.domain.lifecycle import LifecycleAction, RecipeState, VersionStatus
from rastro.domain.lifecycle_history import LifecycleEvent, LifecycleHistory, check_reason
from rastro.domain.memory import RecipeKey

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class Item(BaseModel):
    name: str


KEY = RecipeKey.for_source(Source(url="https://example.com/items", locale="en-US"), Item)


def event(sequence: int) -> LifecycleEvent:
    return LifecycleEvent(
        sequence=sequence,
        version=1,
        from_state=None,
        to_state=RecipeState.ACTIVE,
        action=LifecycleAction.CREATED,
        reason="Created",
        at=NOW,
    )


def serving(version: int) -> VersionStatus:
    return VersionStatus(version=version, state=RecipeState.ACTIVE, validated_at=NOW)


def test_history_allows_at_most_one_serving_version():
    with pytest.raises(ValidationError, match="At most one version"):
        LifecycleHistory(key=KEY, versions=(serving(1), serving(2)), events=())
    assert LifecycleHistory(key=KEY, versions=(serving(1),), events=()).serving == serving(1)


@pytest.mark.parametrize("sequences", [(2, 1), (1, 1)])
def test_history_events_are_strictly_ordered(sequences):
    with pytest.raises(ValidationError, match="strictly increasing"):
        LifecycleHistory(key=KEY, versions=(), events=tuple(event(n) for n in sequences))


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "x" * 501,
        "tab\there",
        "nul\x00",
        5,
        # Reviewer inputs: C1 CSI, bidi override, line separator and blank text.
        "\x9b31mCSI_ESCAPE\x9b0m",
        "bidi\u202etext",
        "sep\u2028x",
        "   ",
    ],
)
def test_reasons_are_bounded_printable_text(reason):
    with pytest.raises(ValidationError):
        check_reason(reason)
    with pytest.raises(ValidationError):
        LifecycleEvent.model_validate({**event(1).model_dump(), "reason": reason})


def test_reasons_accept_unicode_operator_notes():
    assert check_reason("Reparación aprobada ✓") == "Reparación aprobada ✓"
