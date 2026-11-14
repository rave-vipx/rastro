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


def test_cli_notice_follows_the_models_real_remoteness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """P-001: RASTRO_MODEL names a hosted model and RASTRO_ALLOW_REMOTE allows it; without
    --allow-remote Studio must still say page content may go to a remote model."""
    pytest.importorskip("starlette")
    uvicorn = pytest.importorskip("uvicorn")
    from rastro.cli import main

    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    monkeypatch.setenv("RASTRO_MODEL", "http:m@https://api.example.com/v1")
    monkeypatch.setenv("RASTRO_ALLOW_REMOTE", "1")
    assert main(["studio", "--root", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "may go to a remote model" in err and "loopback" not in err
    monkeypatch.setenv("RASTRO_MODEL", "http:m@http://127.0.0.1:11434/v1")
    assert main(["studio", "--root", str(tmp_path), "--allow-remote"]) == 0
    assert "stays on loopback models" in capsys.readouterr().err


def test_is_remote_reads_the_built_model() -> None:
    from rastro.adapters.command_model import CommandModel
    from rastro.adapters.openai_compatible import OpenAICompatibleModel
    from rastro.model_factory import is_remote

    hosted = OpenAICompatibleModel("m", base_url="https://api.example.com/v1", allow_remote=True)
    cloud = OpenAICompatibleModel("m-cloud", base_url="http://127.0.0.1:1234/v1", allow_remote=True)
    plain = OpenAICompatibleModel("m", base_url="http://127.0.0.1:11434/v1")
    assert is_remote(hosted) and is_remote(cloud) and not is_remote(plain, True)
    assert is_remote(CommandModel(["my-cli"], allow_remote=True))
    assert not is_remote(CommandModel(["my-cli"], local=True), True)
    # A port that does not say keeps the operator's declaration.
    assert is_remote(object(), True) and not is_remote(object())
