"""Page steps: bounded actions a recipe may run in a rendered page before extraction.

Steps are data. There is no script step: a recipe can click, type literal text, choose an
option, scroll, wait and follow a link, and nothing else. A followed link is checked again
by the host allowlist and the private-network guard before it is opened.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt, StrictStr, field_validator

from rastro.domain.capability_grants import Capability
from rastro.domain.sources import Contract

__all__ = [
    "MAX_STEPS",
    "ClickStep",
    "FollowStep",
    "PageStep",
    "PageSteps",
    "ScrollStep",
    "SelectStep",
    "TypeStep",
    "WaitState",
    "WaitStep",
    "steps_capabilities",
]

MAX_STEPS = 20
"""The most steps one recipe may run on one page."""

type WaitState = Literal["attached", "visible", "hidden", "detached"]
Selector = Annotated[StrictStr, Field(min_length=1, max_length=1024)]


class _Step(Contract):
    # Typed text may be private (a search term, an account name): keep it out of errors.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("selector", check_fields=False)
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("selector must not be blank")
        return value


class ClickStep(_Step):
    """Click the first element matching ``selector``, then optionally wait for ``wait_for``."""

    action: Literal["click"]
    selector: Selector
    wait_for: Selector | None = None


class TypeStep(_Step):
    """Type literal ``text`` into ``selector``. The text is never read from the environment."""

    action: Literal["type"]
    selector: Selector
    text: StrictStr = Field(max_length=256)


class SelectStep(_Step):
    """Choose the option whose value is ``value`` in the ``<select>`` at ``selector``."""

    action: Literal["select"]
    selector: Selector
    value: StrictStr = Field(max_length=256)


class ScrollStep(_Step):
    """Scroll to the bottom ``times`` times, waiting ``settle_ms`` after each."""

    action: Literal["scroll"]
    times: StrictInt = Field(default=1, ge=1, le=20)
    settle_ms: StrictInt = Field(default=500, ge=0, le=5_000)


class WaitStep(_Step):
    """Wait until ``selector`` reaches ``state``, or for ``timeout_ms`` without a selector."""

    action: Literal["wait"]
    selector: Selector | None = None
    state: WaitState = "visible"
    timeout_ms: StrictInt = Field(default=5_000, ge=0, le=15_000)


class FollowStep(_Step):
    """Open the link read by ``selector``, which must end with ``::attr(href)``."""

    action: Literal["follow"]
    selector: Selector

    @field_validator("selector")
    @classmethod
    def _reads_href(cls, value: str) -> str:
        if not value.endswith("::attr(href)"):
            raise ValueError("follow selector must end with ::attr(href)")
        return value


type PageStep = Annotated[
    ClickStep | TypeStep | SelectStep | ScrollStep | WaitStep | FollowStep,
    Field(discriminator="action"),
]
type PageSteps = Annotated[tuple[PageStep, ...], Field(max_length=MAX_STEPS)]
"""An ordered, bounded list of page steps."""


def steps_capabilities(steps: tuple[PageStep, ...]) -> frozenset[Capability]:
    """The capabilities running ``steps`` needs: ``page_actions`` when there is any step."""
    return frozenset({"page_actions"}) if steps else frozenset()
