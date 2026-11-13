"""`rastro studio --model/--allow-remote` resolves any provider through rastro.model (INT-001,
P-008); nothing is configured unless the user asks."""

from typing import Any

import pytest

import rastro
from rastro.cli_studio import _model
from rastro.domain.models import ConfigurationError


def test_no_spec_and_no_env_means_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RASTRO_MODEL", raising=False)
    assert _model(None, False) is None
    # --allow-remote alone is still the operator's consent for job-declared models.
    assert _model(None, True) is None


def test_studio_model_resolves_through_the_real_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    from rastro.adapters.command_model import CommandModel

    monkeypatch.setenv("RASTRO_MODEL", "command:my-cli --json")
    with pytest.raises(ConfigurationError, match="allow_remote"):
        _model(None, False)
    assert isinstance(_model(None, True), CommandModel)


def test_spec_or_env_goes_through_the_factory_with_the_remote_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    port = object()

    def factory(spec: str | None, *, allow_remote: bool) -> object:
        calls.append((spec, allow_remote))
        return port

    monkeypatch.setattr(rastro, "model", factory, raising=False)
    assert _model("any-provider:any-model", True) is port
    monkeypatch.setenv("RASTRO_MODEL", "command:./my-model")
    assert _model(None, False) is port
    assert calls == [("any-provider:any-model", True), (None, False)]


def test_missing_factory_is_an_explicit_error() -> None:
    if hasattr(rastro, "model"):
        pytest.skip("the rastro.model factory is available")
    with pytest.raises(ConfigurationError, match=r"rastro\.model"):
        _model("x", False)
