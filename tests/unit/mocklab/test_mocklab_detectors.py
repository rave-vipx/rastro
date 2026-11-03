import pytest

from rastro.mocklab.detectors import contains_secret, scrub_url, secret_categories
from rastro.mocklab.seeds import DeterministicStream


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "jwt"),
        ("Authorization: Bearer abcdefgh12345678", "bearer"),
        ("sk_live_abcdefghijklmnop1234", "api_key"),
        ("AKIAABCDEFGHIJKLMNOP", "api_key"),
        ("ghp_" + "a" * 36, "api_key"),
        ("api_key=abcd1234", "credential_assignment"),
        ('"password": "hunter22"', "credential_assignment"),
        ("someone@example.org", "email"),
        ("(415) 555-0199", "phone"),
        ("+44 20 7946 0958", "phone"),
    ],
)
def test_detects_each_category(text, category):
    assert category in secret_categories(text)


@pytest.mark.parametrize("text", ["12.50", "2024-01-01", "Notebook", "article.product", "1.20"])
def test_ordinary_values_are_not_secrets(text):
    assert not contains_secret(text)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://u:p@shop.example.com:8443/a/b?token=x#frag", "https://shop.example.com:8443/a/b"),
        ("/relative/path?q=1", "/relative/path"),
        ("javascript:alert(1)", "#"),
        ("mailto:a@example.org", "#"),
        ("http://[::1", "#"),
    ],
)
def test_scrub_url_keeps_only_scheme_host_and_path(url, expected):
    assert scrub_url(url, DeterministicStream(0, "u")) == expected


def test_scrub_url_replaces_token_like_path_segments():
    scrubbed = scrub_url("/reset/abcdefghijklmnopqrstuvwxyz012345/x", DeterministicStream(0, "u"))
    assert scrubbed.startswith("/reset/") and scrubbed.endswith("/x")
    assert "abcdefghijklmnopqrstuvwxyz012345" not in scrubbed
