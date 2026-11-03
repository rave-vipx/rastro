"""Public lifecycle contracts stay available through the package."""

import rastro
from rastro.domain.lifecycle import (
    LifecycleAction,
    LifecycleTransitionError,
    RecipeState,
    VersionStatus,
)
from rastro.domain.lifecycle_history import LifecycleEvent, LifecycleHistory
from rastro.ports.recipes import RecipeLifecycle


def test_lifecycle_exports() -> None:
    for contract in (
        LifecycleAction,
        LifecycleEvent,
        LifecycleHistory,
        LifecycleTransitionError,
        RecipeLifecycle,
        RecipeState,
        VersionStatus,
    ):
        assert getattr(rastro, contract.__name__) is contract
        assert contract.__name__ in rastro.__all__
