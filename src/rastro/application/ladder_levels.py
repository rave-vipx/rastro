"""The levels of the acquisition ladder and the policy each one fetches with.

Each level adds one capability to the job's own acquisition policy: browser impersonation,
a browser, a stealth browser, challenge solving, rotation over the operator's egress
profiles, and finally the operator's installed solver plugins. A job's own setting is the
floor: the ladder never fetches with less than the job asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.source_notes import LadderLevel

__all__ = ["LEVELS", "PROBE_EVERY", "Rung", "floor_level", "ladder_rungs", "start_plan"]

LEVELS: tuple[LadderLevel, ...] = get_args(LadderLevel.__value__)
"""Every level, from plain HTTP to an operator solver plugin."""
PROBE_EVERY = 10
"""Every this many runs at a remembered level, a run starts again at the job's floor so a
lifted block is noticed and the cheaper level is remembered instead."""
IMPERSONATE = "chrome"
"""The browser the impersonation level presents when the job names none."""


@dataclass(frozen=True)
class Rung:
    """One level and the policy it fetches with."""

    level: LadderLevel
    policy: AcquisitionPolicy


def floor_level(policy: AcquisitionPolicy) -> LadderLevel:
    """The level the job's own policy already asks for; its own proxy rotation is kept on
    every level, so it does not raise the floor."""
    if policy.render == "stealth":
        return "challenge" if policy.solve_challenges else "stealth"
    if policy.render == "browser":
        return "browser"
    return "impersonation" if policy.http.impersonate != "off" else "http"


def _at(policy: AcquisitionPolicy, level: LadderLevel, *, html: bool) -> AcquisitionPolicy:
    rank = LEVELS.index(level)
    if level == "solver_plugin":
        return policy  # plugins fetch with the job's own limits and hosts
    update: dict[str, object] = {}
    browser = html and rank >= LEVELS.index("browser")
    if browser:
        stealth = policy.render == "stealth" or rank >= LEVELS.index("stealth")
        update["render"] = "stealth" if stealth else "browser"
        update["http"] = policy.http  # impersonation is an HTTP transport setting
        if rank >= LEVELS.index("challenge"):
            update["solve_challenges"] = True
    elif rank >= LEVELS.index("impersonation") and policy.http.impersonate == "off":
        update["http"] = policy.http.model_copy(update={"impersonate": IMPERSONATE})
    if rank >= LEVELS.index("proxy_rotation") and not policy.rotate_egress:
        update["rotate_egress"] = tuple(policy.egress_profiles)
    return policy.model_copy(update=update) if update else policy


def ladder_rungs(
    policy: AcquisitionPolicy, *, html: bool, rotate: bool, solvers: bool = True
) -> tuple[Rung, ...]:
    """The rungs to try for ``policy``, from its floor up, each different from the last.

    ``html`` is False for structured sources, which browsers do not fetch. ``rotate`` is the
    operator's egress opt-in: proxy rotation needs it and at least one egress profile.
    ``solvers`` is False when no solver plugin is installed.
    """
    floor = floor_level(policy)
    rungs = [Rung(floor, policy)]  # the job's own policy, exactly as written
    for level in LEVELS[LEVELS.index(floor) + 1 :]:
        if level == "proxy_rotation" and not (rotate and policy.egress_profiles):
            continue
        if level == "solver_plugin":
            if solvers:
                rungs.append(Rung(level, policy))
            continue
        rung = Rung(level, _at(policy, level, html=html))
        if rungs[-1].policy != rung.policy:
            rungs.append(rung)
    return tuple(rungs)


def start_plan(
    floor: LadderLevel, stored: LadderLevel | None, runs: int
) -> tuple[LadderLevel, bool]:
    """Where a run starts and whether it is a probe back at the floor.

    ``stored`` is the remembered level and ``runs`` the runs made at it since it was set.
    """
    if stored is None or LEVELS.index(stored) <= LEVELS.index(floor):
        return floor, False
    if runs + 1 >= PROBE_EVERY:
        return floor, True
    return stored, False
