"""Page steps: the bounded actions a recipe may run in a rendered page."""

import pytest
from pydantic import TypeAdapter, ValidationError

from rastro.domain.page_steps import (
    MAX_STEPS,
    ClickStep,
    FollowStep,
    PageSteps,
    ScrollStep,
    TypeStep,
    WaitStep,
    steps_capabilities,
)

STEPS = TypeAdapter(PageSteps)


def test_page_steps_reject_21_steps_and_unknown_action():
    click = {"action": "click", "selector": "button.more"}
    assert len(STEPS.validate_python([click] * MAX_STEPS)) == 20
    with pytest.raises(ValidationError):
        STEPS.validate_python([click] * (MAX_STEPS + 1))
    with pytest.raises(ValidationError):
        STEPS.validate_python([{"action": "evaluate", "script": "alert(1)"}])


def test_steps_are_discriminated_by_action():
    parsed = STEPS.validate_python(
        [
            {"action": "type", "selector": "input[name=q]", "text": "lamps"},
            {"action": "select", "selector": "select.sort", "value": "price"},
            {"action": "scroll", "times": 3},
            {"action": "wait", "selector": ".results"},
            {"action": "follow", "selector": "a.next::attr(href)"},
        ]
    )
    assert [step.action for step in parsed] == ["type", "select", "scroll", "wait", "follow"]


def test_numbers_and_text_are_strict():
    with pytest.raises(ValidationError):
        ScrollStep(action="scroll", times="3")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ScrollStep(action="scroll", times=21)
    with pytest.raises(ValidationError):
        WaitStep(action="wait", timeout_ms=15_001)
    with pytest.raises(ValidationError):
        TypeStep(action="type", selector="input", text="x" * 257)


def test_errors_do_not_echo_typed_text():
    with pytest.raises(ValidationError) as raised:
        TypeStep(action="type", selector="input", text="hunter2" * 50)
    assert "hunter2" not in str(raised.value)


def test_selectors_are_bounded_and_not_blank():
    with pytest.raises(ValidationError):
        ClickStep(action="click", selector="")
    with pytest.raises(ValidationError):
        ClickStep(action="click", selector="a" * 1025)
    with pytest.raises(ValidationError):
        ClickStep(action="click", selector="   ")


def test_follow_must_read_an_href():
    assert FollowStep(action="follow", selector="a.next::attr(href)").selector.endswith("(href)")
    for selector in ("a.next", "a.next::text", "a.next::attr(src)"):
        with pytest.raises(ValidationError):
            FollowStep(action="follow", selector=selector)


def test_steps_request_the_page_actions_capability():
    assert steps_capabilities(()) == frozenset()
    steps = STEPS.validate_python([{"action": "scroll"}])
    assert steps_capabilities(steps) == {"page_actions"}
