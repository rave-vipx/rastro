"""Capabilities and grants: what each input origin may switch on."""

import pytest

from rastro.domain.capability_grants import (
    CAPABILITIES,
    GRANT_ONLY,
    INPUT_ORIGINS,
    OPERATOR_DEFAULT,
    CapabilityGrants,
    decide,
    default_grants,
)
from rastro.domain.sources import ConfigurationError


def test_decide_operator_gets_defaults_not_grant_only():
    decision = decide(frozenset({"stealth", "cdp"}), "operator", default_grants())
    assert decision.granted == {"stealth"}
    assert decision.refused == {"cdp"}


def test_decide_mcp_refuses_stealth_by_default():
    decision = decide(frozenset({"stealth"}), "mcp", default_grants())
    assert decision.granted == frozenset()
    assert decision.refused == {"stealth"}


def test_operator_default_is_everything_but_grant_only():
    assert frozenset(CAPABILITIES) - GRANT_ONLY == OPERATOR_DEFAULT
    assert {"persistent_session", "real_chrome", "cdp", "solver_plugins"} == GRANT_ONLY
    assert INPUT_ORIGINS == ("operator", "mcp", "bundle", "registry", "model")


def test_operator_grants_add_grant_only_capabilities():
    grants = default_grants(operator_grants=frozenset({"cdp"}))
    assert grants.allows("cdp", "operator")
    assert not grants.allows("cdp", "bundle")


def test_untrusted_origins_get_only_what_is_granted_to_them():
    grants = default_grants(untrusted={"registry": frozenset({"stealth"})})
    assert grants.allows("stealth", "registry")
    assert not grants.allows("stealth", "bundle")
    assert not grants.allows("proxy_rotation", "registry")


def test_untrusted_grants_cannot_target_the_operator_origin():
    with pytest.raises(ConfigurationError):
        default_grants(untrusted={"operator": frozenset({"cdp"})})


def test_unknown_names_fail_closed():
    with pytest.raises(ConfigurationError):
        default_grants(operator_grants=frozenset({"teleport"}))  # type: ignore[arg-type]
    decision = decide(frozenset({"teleport"}), "operator", default_grants())  # type: ignore[arg-type]
    assert decision.refused == {"teleport"}
    assert not default_grants().allows("stealth", "stranger")  # type: ignore[arg-type]


def test_grants_cannot_be_mutated_after_construction():
    grants = default_grants()
    with pytest.raises(TypeError):
        grants.by_origin["mcp"] = frozenset({"stealth"})  # type: ignore[index]
    source = {"bundle": frozenset({"stealth"})}
    held = CapabilityGrants(by_origin=source)  # type: ignore[arg-type]
    source["bundle"] = frozenset({"cdp"})
    assert held.allows("stealth", "bundle")


def test_require_passes_when_nothing_is_refused():
    decide(frozenset({"stealth", "http3"}), "operator", default_grants()).require()


def test_require_names_refused_capabilities_and_the_flag():
    decision = decide(frozenset({"stealth", "proxy_rotation"}), "mcp", default_grants())
    with pytest.raises(ConfigurationError) as raised:
        decision.require()
    message = str(raised.value)
    assert "proxy_rotation, stealth" in message
    assert "mcp" in message
    assert raised.value.hint is not None
    assert "--allow-untrusted mcp:proxy_rotation" in raised.value.hint


def test_operator_refusal_points_at_allow_capability():
    with pytest.raises(ConfigurationError) as raised:
        decide(frozenset({"real_chrome"}), "operator", default_grants()).require()
    assert raised.value.hint == "Pass --allow-capability real_chrome to grant it."


def test_refusal_message_never_echoes_unknown_input():
    with pytest.raises(ConfigurationError) as raised:
        decide(frozenset({"$(rm -rf ~)"}), "mcp", default_grants()).require()  # type: ignore[arg-type]
    assert "rm -rf" not in str(raised.value)
    assert "rm -rf" not in (raised.value.hint or "")
