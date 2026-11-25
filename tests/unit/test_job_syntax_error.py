"""A job file with invalid JSON raises Rastro's own error, not a raw decoder exception."""

import json

import pytest

import rastro
from rastro.cli_errors import classify


def test_invalid_json_job_raises_configuration_error() -> None:
    with pytest.raises(
        rastro.ConfigurationError, match=r"not valid JSON \(line 1, column"
    ) as raised:
        rastro.parse_job('{"format_version": 1,', format="json")
    assert not isinstance(raised.value, json.JSONDecodeError)


def test_invalid_json_job_file_keeps_the_syntax_error_envelope(tmp_path) -> None:
    path = tmp_path / "job.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(rastro.ConfigurationError) as raised:
        rastro.load_job(path)
    payload = classify(raised.value)
    assert payload["error"] == "syntax_error"
    assert "not valid JSON (line 1, column 2)" in payload["message"]
