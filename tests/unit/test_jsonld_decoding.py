"""INT-002, N23: JSON-LD tolerance independently of HTML-parser normalization."""

import json

import pytest

from rastro.adapters.scrapling import _jsonld_payload


@pytest.mark.parametrize("control", [chr(i) for i in range(32)])
def test_raw_controls_are_preserved_before_html_normalization(control):
    """INT-002, N23: the decoder preserves all controls, including NUL and CR."""
    description = 'one "quoted" \\ path' + control + "two"
    raw = json.dumps({"description": description})
    raw = raw.replace(json.dumps(control)[1:-1], control)
    issues = []
    assert _jsonld_payload(raw, issues) == {"description": description}
    assert [issue.code for issue in issues] == ["repaired_json"]
    assert issues[0].message is None


def test_tolerance_does_not_escape_controls_outside_strings():
    """INT-002, N23: control characters must never be repaired as JSON structure."""
    issues = []
    with pytest.raises(ValueError):
        _jsonld_payload('{"name":"A"\x01,"description":"one\ntwo"}', issues)
    assert issues == []
