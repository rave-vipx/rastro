"""Every boolean RASTRO_ALLOW_* opt-in reads the same values (SEC-004/005, security audit)."""

import pytest

from rastro.application.operator_trust import current_trust, env_flag


@pytest.mark.parametrize("value", ["1", "true", "yes", " TRUE ", "Yes"])
def test_true_values(value: str) -> None:
    assert env_flag("RASTRO_ALLOW_EGRESS", {"RASTRO_ALLOW_EGRESS": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "on", "2", "y"])
def test_anything_else_is_off(value: str) -> None:
    assert env_flag("RASTRO_ALLOW_REMOTE", {"RASTRO_ALLOW_REMOTE": value}) is False
    assert env_flag("RASTRO_ALLOW_REMOTE", {}) is False


def test_remote_trust_uses_the_shared_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RASTRO_ALLOW_REMOTE", "Yes")
    assert current_trust().allow_remote is True
    monkeypatch.setenv("RASTRO_ALLOW_REMOTE", "on")
    assert current_trust().allow_remote is False
