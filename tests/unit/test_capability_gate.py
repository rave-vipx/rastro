"""The capability gate: the entry point decides the origin, the origin decides the grants."""

from __future__ import annotations

import pytest

from rastro.application.capability_gate import (
    CapabilityRefused,
    requested_capabilities,
    require,
    require_capabilities,
)
from rastro.application.operator_trust import (
    current_grants,
    current_origin,
    input_origin,
    operator_trust,
    set_process_trust,
)
from rastro.domain.capability_grants import GRANT_ONLY, OPERATOR_DEFAULT
from rastro.domain.jobs import Job
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

SECRET = "s3cr3t-value-Qx7"


def job(*, render="http", robots=None, solve=False, per_source=None) -> Job:
    source: dict[str, object] = {"url": "https://example.com/"}
    if per_source:
        source["render"] = per_source
    acquisition: dict[str, object] = {
        "allowed_hosts": ["example.com"],
        "render": render,
        "solve_challenges": solve,
    }
    if robots:
        acquisition["robots"] = robots
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [source],
            "fields": [{"name": "title", "type": "string"}],
            "recipe": {"strategy": "css", "record_selector": "p", "fields": {"title": "::text"}},
            "acquisition": acquisition,
        }
    )


@pytest.fixture(autouse=True)
def no_env_grants(monkeypatch):
    for name in ("RASTRO_ALLOW_STEALTH", "RASTRO_ALLOW_CAPABILITIES", "RASTRO_ALLOW_UNTRUSTED"):
        monkeypatch.delenv(name, raising=False)


def test_settings_request_their_capabilities():
    assert requested_capabilities(job()) == frozenset()
    assert requested_capabilities(job(render="stealth")) == {"stealth"}
    assert requested_capabilities(job(per_source="stealth")) == {"stealth"}
    assert requested_capabilities(job(render="browser", solve=True)) == {"solve_challenges"}
    assert requested_capabilities(job(robots="ignore")) == {"robots_ignore"}


def test_operator_origin_is_the_default_and_gets_every_default_capability():
    assert current_origin() == "operator"
    grants = current_grants()
    assert grants.by_origin["operator"] == OPERATOR_DEFAULT
    assert all(grants.by_origin[o] == frozenset() for o in ("mcp", "bundle", "registry", "model"))
    decision = require_capabilities(job(render="stealth", robots="ignore", solve=True))
    assert decision.granted == {"stealth", "robots_ignore", "solve_challenges"}


@pytest.mark.parametrize("origin", ["mcp", "bundle", "registry", "model"])
def test_untrusted_origins_are_refused_without_a_grant(origin):
    with input_origin(origin), pytest.raises(CapabilityRefused) as caught:
        require_capabilities(job(render="stealth", robots="ignore"))
    error = caught.value
    assert error.error_code == "capability_refused"
    assert error.decision.refused == {"stealth", "robots_ignore"}
    assert f"--allow-untrusted {origin}:robots_ignore" in (error.hint or "")
    assert "example.com" not in str(error)


def test_a_grant_widens_only_its_origin():
    previous = set_process_trust(OperatorTrust(untrusted_capabilities={"mcp": {"stealth"}}))
    try:
        with input_origin("mcp"):
            assert require_capabilities(job(render="stealth")).granted == {"stealth"}
        with input_origin("bundle"), pytest.raises(CapabilityRefused):
            require_capabilities(job(render="stealth"))
    finally:
        set_process_trust(previous)


def test_grant_only_capabilities_need_an_explicit_grant_even_for_the_operator():
    with pytest.raises(CapabilityRefused) as caught:
        require(frozenset({"cdp"}))
    assert "--allow-capability cdp" in (caught.value.hint or "")
    previous = set_process_trust(OperatorTrust(operator_grants=frozenset({"cdp"})))
    try:
        assert require(frozenset({"cdp"})).granted == {"cdp"}
    finally:
        set_process_trust(previous)
    assert GRANT_ONLY.isdisjoint(OPERATOR_DEFAULT)


def test_untrusted_input_cannot_switch_back_to_the_operator_origin():
    with input_origin("mcp"):
        with pytest.raises(ConfigurationError), input_origin("operator"):
            pass
        assert current_origin() == "mcp"
    assert current_origin() == "operator"


def test_the_deprecated_stealth_switch_only_widens_untrusted_origins():
    with operator_trust(allow_stealth=True), input_origin("model"):
        decision = require_capabilities(job(render="browser", per_source="stealth", solve=True))
        assert decision.granted == {"stealth", "solve_challenges"}
        with pytest.raises(CapabilityRefused):
            require_capabilities(job(robots="ignore"))


def test_environment_grants_are_read_and_unknown_names_never_echoed(monkeypatch):
    monkeypatch.setenv("RASTRO_ALLOW_UNTRUSTED", "registry:robots_ignore, mcp:stealth")
    monkeypatch.setenv("RASTRO_ALLOW_CAPABILITIES", "real_chrome")
    grants = current_grants()
    assert grants.allows("robots_ignore", "registry")
    assert grants.allows("stealth", "mcp") and not grants.allows("stealth", "bundle")
    assert grants.allows("real_chrome", "operator")
    for env, value in (
        ("RASTRO_ALLOW_CAPABILITIES", SECRET),
        ("RASTRO_ALLOW_UNTRUSTED", f"mcp:{SECRET}"),
        ("RASTRO_ALLOW_UNTRUSTED", f"operator:stealth,{SECRET}"),
        ("RASTRO_ALLOW_UNTRUSTED", "stealth"),
    ):
        monkeypatch.setenv(env, value)
        with pytest.raises(ConfigurationError) as caught:
            current_grants()
        assert SECRET not in f"{caught.value} {caught.value.hint}"
        monkeypatch.delenv(env)
