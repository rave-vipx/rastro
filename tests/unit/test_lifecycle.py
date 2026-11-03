from datetime import UTC, datetime
from itertools import product

import pytest
from pydantic import ValidationError

from rastro.domain.lifecycle import (
    SERVING_STATES,
    LifecycleAction,
    LifecycleChange,
    LifecycleTransitionError,
    RecipeState,
    VersionStatus,
    plan_creation,
    plan_rollback,
    plan_transition,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)
C, A, D, R = RecipeState.CANDIDATE, RecipeState.ACTIVE, RecipeState.DEGRADED, RecipeState.RETIRED

ALLOWED = {
    (C, A): LifecycleAction.PROMOTED,
    (C, R): LifecycleAction.REJECTED,
    (A, D): LifecycleAction.DEGRADED,
    (A, R): LifecycleAction.RETIRED,
    (D, A): LifecycleAction.RECOVERED,
    (D, R): LifecycleAction.RETIRED,
}


def status(version: int, state: RecipeState, *, validated: bool | None = None) -> VersionStatus:
    if validated is None:
        validated = state is not C
    return VersionStatus(version=version, state=state, validated_at=NOW if validated else None)


def change(version, before, after, action) -> LifecycleChange:
    return LifecycleChange(version=version, from_state=before, to_state=after, action=action)


@pytest.mark.parametrize(("before", "after"), sorted(ALLOWED))
def test_every_allowed_transition_names_its_action(before, after):
    serving = None if before in SERVING_STATES else status(1, A)
    planned = plan_transition(status(2, before), after, serving=serving)
    assert planned[-1] == change(2, before, after, ALLOWED[before, after])


@pytest.mark.parametrize(
    ("before", "after"), [pair for pair in product(RecipeState, repeat=2) if pair not in ALLOWED]
)
def test_every_other_transition_is_forbidden(before, after):
    serving = None if before in SERVING_STATES else status(1, A)
    with pytest.raises(LifecycleTransitionError, match=f"{before.value} to {after.value}"):
        plan_transition(status(2, before, validated=before is not C), after, serving=serving)


def test_promotion_supersedes_the_serving_version_first():
    for serving_state in SERVING_STATES:
        planned = plan_transition(status(2, C), A, serving=status(1, serving_state))
        assert planned == (
            change(1, serving_state, R, LifecycleAction.SUPERSEDED),
            change(2, C, A, LifecycleAction.PROMOTED),
        )


def test_recovery_and_degradation_do_not_supersede_themselves():
    assert plan_transition(status(1, D), A, serving=status(1, D)) == (
        change(1, D, A, LifecycleAction.RECOVERED),
    )
    assert plan_transition(status(1, A), D, serving=status(1, A)) == (
        change(1, A, D, LifecycleAction.DEGRADED),
    )


def test_rejecting_a_candidate_leaves_the_serving_version_alone():
    assert plan_transition(status(2, C), R, serving=status(1, A)) == (
        change(2, C, R, LifecycleAction.REJECTED),
    )


def test_creation_of_active_and_candidate_versions():
    assert plan_creation(1, candidate=False, serving=None) == (
        change(1, None, A, LifecycleAction.CREATED),
    )
    assert plan_creation(2, candidate=False, serving=status(1, D)) == (
        change(1, D, R, LifecycleAction.SUPERSEDED),
        change(2, None, A, LifecycleAction.CREATED),
    )
    # REC-006: a candidate never displaces the serving version.
    assert plan_creation(2, candidate=True, serving=status(1, A)) == (
        change(2, None, C, LifecycleAction.CREATED),
    )


def test_lower_new_version_never_displaces_a_newer_serving_version():
    assert plan_creation(2, candidate=False, serving=status(3, A)) == (
        change(2, None, C, LifecycleAction.CREATED),
    )


def test_rollback_restores_a_validated_retired_version():
    assert plan_rollback(status(1, R), serving=status(3, D)) == (
        change(3, D, R, LifecycleAction.SUPERSEDED),
        change(1, R, A, LifecycleAction.ROLLED_BACK),
    )
    assert plan_rollback(status(1, R), serving=None) == (
        change(1, R, A, LifecycleAction.ROLLED_BACK),
    )


@pytest.mark.parametrize(
    "target",
    [
        status(2, R, validated=False),  # A rejected candidate was never validated.
        status(2, C),
        status(2, A),
        status(2, D),
    ],
)
def test_rollback_rejects_unvalidated_or_serving_targets(target):
    with pytest.raises(LifecycleTransitionError, match="previously validated"):
        plan_rollback(target, serving=status(1, A))


def test_version_status_invariants():
    with pytest.raises(ValidationError, match="validation"):
        VersionStatus(version=1, state=A, validated_at=None)
    with pytest.raises(ValidationError, match="validation"):
        VersionStatus(version=1, state=D, validated_at=None)
    with pytest.raises(ValidationError, match="never been validated"):
        VersionStatus(version=1, state=C, validated_at=NOW)
    with pytest.raises(ValidationError):
        VersionStatus(version=0, state=R, validated_at=None)
    assert status(1, A).serving and not status(1, R).serving


def test_states_serialize_as_stable_strings():
    assert [state.value for state in RecipeState] == ["candidate", "active", "degraded", "retired"]
    assert frozenset({A, D}) == SERVING_STATES
    payload = change(1, None, A, LifecycleAction.CREATED).model_dump(mode="json")
    assert payload == {"version": 1, "from_state": None, "to_state": "active", "action": "created"}
