"""Stored page_url keeps scheme, host, port and path only (N3, SEC-008, OBS-001)."""

import pytest

from rastro.domain.provenance_url import provenance_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://u:p@h.example/list;jsessionid=X?page=2&token=abc#f",
            "https://h.example/list",
        ),
        ("https://h.example:8443/a/b?q=1", "https://h.example:8443/a/b"),
        ("https://H.Example/", "https://h.example/"),
        ("https://[2001:db8::1]:8080/x?y=1", "https://[2001:db8::1]:8080/x"),
        ("https://h.example", "https://h.example"),
        ("https://h.example/p;a=b/q;c", "https://h.example/p/q"),
    ],
)
def test_provenance_url_drops_secrets_and_state(raw, expected):
    assert provenance_url(raw) == expected


def test_unparseable_url_never_echoes_input():
    assert provenance_url("https://h.example:99999/x?token=abc") == "https://h.example/x"
    assert "token" not in provenance_url("not a url?token=abc")
