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
        ("/a/b?q=1", "/a/b"),
        ("javascript:alert(1)", "#"),
        ("mailto:a@example.org", "#"),
        ("http://[::1", "#"),
    ],
)
def test_scrub_url_keeps_only_scheme_host_and_recipe_named_segments(url, expected):
    assert scrub_url(url, DeterministicStream(0, "u"), frozenset({"a", "b"})) == expected


def test_scrub_url_replaces_unnamed_and_token_like_path_segments():
    token = "abcdefghijklmnopqrstuvwxyz012345"
    scrubbed = scrub_url(f"/reset/{token}/x", DeterministicStream(0, "u"), frozenset({token}))
    assert scrubbed.count("/") == 3 and token not in scrubbed and "reset" not in scrubbed


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("123-45-6789", "ssn"),
        ("4111-1111-1111-1111", "card"),
        ("GB82WEST12345698765432", "iban"),
        ("dXNlcjpwYXNzd29yZA==", "base64_credential"),
        ("9f8a7b6c5d4e3f2a1b0c9d8e", "hex_token"),
        ("Kx9QpL2vN8rT4wZ1aB3cD5", "opaque_token"),
        ("/p?sessionid=abc", "url_secret_param"),
        ("jane.doe%40corp.example", "email"),
    ],
)
def test_detects_review_categories(text, category):
    assert category in secret_categories(text)


@pytest.mark.parametrize(
    "text", ["4111 1111 1111 1112", "GB00 WEST 1234 5698 7654 32", "order 12345", "product-card"]
)
def test_checksum_detectors_reject_invalid_numbers(text):
    assert not contains_secret(text)
