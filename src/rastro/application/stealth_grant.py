"""The one check that a stealth request has the operator's grant (SEC-005, ADR 0034)."""

from __future__ import annotations

from rastro.application.operator_trust import current_trust
from rastro.domain.models import ConfigurationError

STEALTH_REFUSED = "Stealth requires --allow-stealth, allow_stealth=True or RASTRO_ALLOW_STEALTH=1"


def require_stealth_grant(*, stealth: bool, solve_challenges: bool, allow: bool = False) -> None:
    """Refuse before any renderer exists. Plain browser and scroll never reach this."""
    if (stealth or solve_challenges) and not (allow is True or current_trust().allow_stealth):
        raise ConfigurationError(STEALTH_REFUSED)
