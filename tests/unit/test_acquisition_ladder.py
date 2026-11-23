"""Ladder levels, the order they are tried in, and the capability gate for each."""

from __future__ import annotations

import pytest

from rastro.application.acquisition_ladder import climb, gate
from rastro.application.ladder_levels import (
    LEVELS,
    PROBE_EVERY,
    floor_level,
    ladder_rungs,
    start_plan,
)
from rastro.application.operator_trust import input_origin, set_process_trust
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

POLICY = AcquisitionPolicy(allowed_hosts=("ladder.test",))
PROXIES = AcquisitionPolicy.model_validate(
    {
        "allowed_hosts": ["ladder.test"],
        "egress_profiles": {
            "one": {"proxy_url": "http://proxy-one.example:8080"},
            "two": {"proxy_url": "http://proxy-two.example:8080"},
        },
    }
)


def levels(**kwargs) -> list[str]:
    return [rung.level for rung in ladder_rungs(kwargs.pop("policy", POLICY), **kwargs)]


def test_levels_run_from_plain_http_to_operator_solvers() -> None:
    assert LEVELS == (
        "http",
        "impersonation",
        "browser",
        "stealth",
        "challenge",
        "proxy_rotation",
        "solver_plugin",
    )


def test_each_rung_adds_one_capability_to_the_job_policy() -> None:
    rungs = {r.level: r.policy for r in ladder_rungs(PROXIES, html=True, rotate=True)}
    assert rungs["http"] == PROXIES
    assert rungs["impersonation"].http.impersonate == "chrome"
    assert rungs["browser"].render == "browser"
    assert rungs["browser"].http.impersonate == "off"
    assert rungs["stealth"].render == "stealth" and not rungs["stealth"].solve_challenges
    assert rungs["challenge"].solve_challenges
    assert rungs["proxy_rotation"].rotate_egress == ("one", "two")
    assert rungs["solver_plugin"] == PROXIES


def test_proxy_rotation_needs_profiles_and_the_egress_opt_in() -> None:
    assert "proxy_rotation" not in levels(html=True, rotate=True)  # no profiles
    assert "proxy_rotation" not in levels(policy=PROXIES, html=True, rotate=False)
    assert "proxy_rotation" in levels(policy=PROXIES, html=True, rotate=True)


def test_structured_sources_skip_browser_levels() -> None:
    assert levels(html=False, rotate=False) == ["http", "impersonation", "solver_plugin"]


def test_the_job_setting_is_the_floor() -> None:
    stealth = POLICY.model_copy(update={"render": "stealth"})
    assert floor_level(stealth) == "stealth"
    assert levels(policy=stealth, html=True, rotate=False) == [
        "stealth",
        "challenge",
        "solver_plugin",
    ]


def test_start_plan_uses_memory_and_probes_the_floor_periodically() -> None:
    assert start_plan("http", None, 0) == ("http", False)
    assert start_plan("http", "browser", 0) == ("browser", False)
    assert start_plan("stealth", "browser", 3) == ("stealth", False)  # never below the job
    assert start_plan("http", "browser", PROBE_EVERY - 1) == ("http", True)


def _blocked_until(level: str):
    tried: list[str] = []

    def attempt(rung):
        tried.append(rung.level)
        return rung.level, "ok" if rung.level == level else "blocked"

    return attempt, tried


def test_climb_stops_at_the_first_working_level() -> None:
    attempt, tried = _blocked_until("browser")
    result, steps, working = climb(
        ladder_rungs(POLICY, html=True, rotate=False), "http", attempt, lambda rung: True
    )
    assert (result, working.level) == ("browser", "browser")
    assert tried == ["http", "impersonation", "browser"]
    assert [(s.level, s.outcome) for s in steps][-1] == ("browser", "ok")


def test_climb_records_a_level_that_cannot_be_built_and_goes_on() -> None:
    def attempt(rung):
        if rung.level == "browser":
            raise ConfigurationError("Install the browser extra")
        return rung.level, "ok" if rung.level == "stealth" else "blocked"

    _, steps, working = climb(
        ladder_rungs(POLICY, html=True, rotate=False), "http", attempt, lambda rung: True
    )
    assert ("browser", "failed") in [(s.level, s.outcome) for s in steps]
    assert working.level == "stealth"


def test_a_failure_that_is_not_a_block_stops_the_climb() -> None:
    _, steps, working = climb(
        ladder_rungs(POLICY, html=True, rotate=False),
        "http",
        lambda rung: (rung.level, "failed"),
        lambda rung: True,
    )
    assert [(s.level, s.outcome) for s in steps] == [("http", "failed")]
    assert working is None


@pytest.mark.parametrize("origin", ["mcp", "bundle", "registry", "model"])
def test_ladder_skips_not_granted_levels(origin) -> None:
    attempt, tried = _blocked_until("never")
    with input_origin(origin):
        _, steps, working = climb(
            ladder_rungs(PROXIES, html=True, rotate=True), "http", attempt, gate
        )
    assert working is None
    assert tried == ["http", "browser"]  # the only levels that need no grant
    assert [(s.level, s.outcome) for s in steps] == [
        ("http", "blocked"),
        ("impersonation", "not_granted"),
        ("browser", "blocked"),
        ("stealth", "not_granted"),
        ("challenge", "not_granted"),
        ("proxy_rotation", "not_granted"),
        ("solver_plugin", "not_granted"),
    ]


def test_a_grant_opens_its_level_for_an_untrusted_origin() -> None:
    attempt, tried = _blocked_until("stealth")
    previous = set_process_trust(OperatorTrust(untrusted_capabilities={"mcp": {"stealth"}}))
    try:
        with input_origin("mcp"):
            climb(ladder_rungs(POLICY, html=True, rotate=False), "http", attempt, gate)
    finally:
        set_process_trust(previous)
    assert tried == ["http", "browser", "stealth"]


def test_solver_plugins_need_an_explicit_grant_even_for_the_operator() -> None:
    solver = ladder_rungs(POLICY, html=True, rotate=False)[-1]
    assert solver.level == "solver_plugin"
    assert gate(solver) is False
    previous = set_process_trust(OperatorTrust(operator_grants=frozenset({"solver_plugins"})))
    try:
        assert gate(solver) is True
    finally:
        set_process_trust(previous)
