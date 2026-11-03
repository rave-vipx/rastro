"""Machine-readable lifecycle history for one recipe key."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, Field, TypeAdapter, model_validator

from rastro.domain.lifecycle import LifecycleAction, RecipeState, VersionStatus
from rastro.domain.memory import RecipeKey
from rastro.domain.sources import Contract

# Controls (C0, C1 and so ANSI escape introducers), surrogates, line and paragraph
# separators could spoof terminal or log output. Other format characters stay allowed
# so emoji ZWJ sequences work; bidi controls are rejected explicitly.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def _printable(reason: str) -> str:
    if not reason.strip() or any(
        character in _BIDI_CONTROLS or unicodedata.category(character) in _FORBIDDEN_CATEGORIES
        for character in reason
    ):
        raise ValueError("Reasons must be non-blank printable text")
    return reason


# Operator notes only, never raw documents, records or credentials.
type Reason = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(_printable)]
_REASON: TypeAdapter[str] = TypeAdapter(Reason)


def check_reason(reason: str) -> str:
    """Return ``reason`` if it is 1-500 non-blank printable characters; raise otherwise."""
    return _REASON.validate_python(reason, strict=True)


class LifecycleEvent(Contract):
    """One recorded state change of a stored version, in key-wide order."""

    sequence: int = Field(ge=1)
    version: int = Field(ge=1, le=2_147_483_647)
    from_state: RecipeState | None
    to_state: RecipeState
    action: LifecycleAction
    reason: Reason
    at: AwareDatetime


class LifecycleHistory(Contract):
    """Current version states and every lifecycle event for one recipe key."""

    key: RecipeKey
    versions: tuple[VersionStatus, ...]
    events: tuple[LifecycleEvent, ...]

    @model_validator(mode="after")
    def single_serving_version(self) -> Self:
        if sum(item.serving for item in self.versions) > 1:
            raise ValueError("At most one version may serve a recipe key")
        if [item.sequence for item in self.events] != sorted(
            {item.sequence for item in self.events}
        ):
            raise ValueError("Lifecycle events must be in strictly increasing order")
        return self

    @property
    def serving(self) -> VersionStatus | None:
        """The active or degraded version used when no version is pinned."""
        return next((item for item in self.versions if item.serving), None)
