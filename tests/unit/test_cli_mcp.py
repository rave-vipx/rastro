"""`rastro mcp` degrades clearly without the extra (AGENT-002)."""

import builtins
import json
from typing import Any

import pytest

from rastro.cli import main


def test_missing_extra_exits_nonzero_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def deny(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rastro.adapters.mcp_server":
            raise ImportError("no mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    assert main(["mcp"]) != 0
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "missing_extra" and "rastro-engine[mcp]" in error["hint"]


def test_missing_root_is_a_config_error(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("mcp")
    assert main(["mcp", "--root", str(tmp_path / "absent")]) != 0
