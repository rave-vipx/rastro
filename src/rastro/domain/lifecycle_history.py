"""Machine-readable lifecycle history for one recipe key."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, Field, TypeAdapter, model_validator

from rastro.domain.lifecycle import LifecycleAction, RecipeState, VersionStatus
from rastro.domain.memory import RecipeKey
from rastro.domain.sources import Contract


def _printable(reason: str) -> str:
    # isprintable() rejects C0/C1 controls, ANSI escape introducers, bidi overrides and
    # line or paragraph separators, so a reason cannot spoof terminal or log output.
    if not reason.isprintable() or not reason.strip():
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
