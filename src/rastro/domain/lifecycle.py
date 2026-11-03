"""Recipe lifecycle states and the pure rules that move stored versions between them.

Exactly one version per recipe key may serve reuse at a time: the one that is
``active`` or ``degraded``. Planning functions return the ordered changes a store
must apply atomically; they perform no I/O and hold no state.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from rastro.domain.sources import Contract, RastroError


class RecipeState(StrEnum):
    """Lifecycle state of one stored recipe version (REC-005)."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEGRADED = "degraded"
    RETIRED = "retired"


SERVING_STATES = frozenset({RecipeState.ACTIVE, RecipeState.DEGRADED})


class LifecycleAction(StrEnum):
    """Why a version changed state; recorded with every lifecycle event."""

    CREATED = "created"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    DEGRADED = "degraded"
    RECOVERED = "recovered"
    RETIRED = "retired"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"
    MIGRATED = "migrated"


# Retired versions return to service only through ``plan_rollback``.
_TRANSITIONS: Mapping[tuple[RecipeState, RecipeState], LifecycleAction] = {
    (RecipeState.CANDIDATE, RecipeState.ACTIVE): LifecycleAction.PROMOTED,
    (RecipeState.CANDIDATE, RecipeState.RETIRED): LifecycleAction.REJECTED,
    (RecipeState.ACTIVE, RecipeState.DEGRADED): LifecycleAction.DEGRADED,
    (RecipeState.ACTIVE, RecipeState.RETIRED): LifecycleAction.RETIRED,
    (RecipeState.DEGRADED, RecipeState.ACTIVE): LifecycleAction.RECOVERED,
    (RecipeState.DEGRADED, RecipeState.RETIRED): LifecycleAction.RETIRED,
}


class LifecycleTransitionError(RastroError):
    """The requested lifecycle change is not allowed from the version's current state."""


class VersionStatus(Contract):
    """Lifecycle position of one stored version.

    ``validated_at`` is when the version first served as active. Only versions that
    have served are eligible for rollback (REPAIR-006).
    """

    version: int = Field(ge=1, le=2_147_483_647)
    state: RecipeState
    validated_at: AwareDatetime | None

    @model_validator(mode="after")
    def consistent_validation(self) -> Self:
        if self.state in SERVING_STATES and self.validated_at is None:
            raise ValueError("Serving versions require a validation timestamp")
        if self.state is RecipeState.CANDIDATE and self.validated_at is not None:
            raise ValueError("Candidates have never been validated for service")
        return self

    @property
    def serving(self) -> bool:
        return self.state in SERVING_STATES


class LifecycleChange(Contract):
    """One planned state change; ``from_state`` is ``None`` for a newly stored version."""

    version: int = Field(ge=1, le=2_147_483_647)
    from_state: RecipeState | None
    to_state: RecipeState
    action: LifecycleAction


def _supersede(serving: VersionStatus | None, replacement: int) -> tuple[LifecycleChange, ...]:
    if serving is None or serving.version == replacement:
        return ()
    return (
        LifecycleChange(
            version=serving.version,
            from_state=serving.state,
            to_state=RecipeState.RETIRED,
            action=LifecycleAction.SUPERSEDED,
        ),
    )


def plan_creation(
    version: int, *, candidate: bool, serving: VersionStatus | None
) -> tuple[LifecycleChange, ...]:
    """Store a new version as a candidate, or as active by replacing the serving one.

    A candidate never displaces the serving version (REC-006). A new version lower
    than the serving one is also stored as a candidate, so a request that completes
    late cannot replace a newer recipe.
    """
    outranked = serving is not None and serving.version > version
    candidate = candidate or outranked
    state = RecipeState.CANDIDATE if candidate else RecipeState.ACTIVE
    created = LifecycleChange(
        version=version, from_state=None, to_state=state, action=LifecycleAction.CREATED
    )
    return (created,) if candidate else (*_supersede(serving, version), created)


def plan_transition(
    target: VersionStatus, to: RecipeState, *, serving: VersionStatus | None
) -> tuple[LifecycleChange, ...]:
    """Plan an allowed transition, superseding the serving version on promotion."""
    action = _TRANSITIONS.get((target.state, to))
    if action is None:
        raise LifecycleTransitionError(
            f"Cannot move a recipe from {target.state.value} to {to.value}"
        )
    changed = LifecycleChange(
        version=target.version, from_state=target.state, to_state=to, action=action
    )
    superseded = _supersede(serving, target.version) if to is RecipeState.ACTIVE else ()
    return (*superseded, changed)


def plan_rollback(
    target: VersionStatus, *, serving: VersionStatus | None
) -> tuple[LifecycleChange, ...]:
    """Return a retired, previously validated version to service (REPAIR-006)."""
    if target.state is not RecipeState.RETIRED or target.validated_at is None:
        raise LifecycleTransitionError(
            "Rollback requires a retired, previously validated recipe version"
        )
    restored = LifecycleChange(
        version=target.version,
        from_state=RecipeState.RETIRED,
        to_state=RecipeState.ACTIVE,
        action=LifecycleAction.ROLLED_BACK,
    )
    return (*_supersede(serving, target.version), restored)
