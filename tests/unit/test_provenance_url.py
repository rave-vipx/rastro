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


def test_canonical_keeps_page_query_strips_tracking():
    from rastro.domain.provenance_url import canonical_page_url

    assert (
        canonical_page_url("https://catalogue.example/?utm_source=x&page=2&b=1#top")
        == "https://catalogue.example/?b=1&page=2"
    )


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "key",
        "sig",
        "signature",
        "session",
        "sid",
        "auth",
        "password",
        "code",
        "state",
        "access_token",
        "API-Key",
        "X-Amz-Signature",
        "%74oken",
        "TOKEN",
        "client_secret",
    ],
)
def test_canonical_drops_secret_like(key):
    from rastro.domain.provenance_url import canonical_page_url

    assert (
        canonical_page_url(f"https://catalogue.example/?{key}=hidden&page=3")
        == "https://catalogue.example/?page=3"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://u:p@H.example:8443/x;session=hidden?b=&a=2&a=1#private",
            "https://h.example:8443/x?a=1&a=2&b=",
        ),
        (
            "https://h.example/?FBCLID=hidden&gclid=hidden&mc_eid=hidden&q=blue+bird",
            "https://h.example/?q=blue+bird",
        ),
        ("https://[broken/?token=hidden", ""),
    ],
)
def test_canonical_url_preserves_safe_components(raw, expected):
    from rastro.domain.provenance_url import canonical_page_url

    assert canonical_page_url(raw) == expected
    assert "?" not in provenance_url(raw)


@pytest.mark.parametrize(
    "key",
    ["jsessionid", "PHPSESSID", "accessToken", "AWSAccessKeyId", "apikey", "oauth_token", "nonce"],
)
def test_canonical_drops_compound_secret_keys(key):
    from rastro.domain.provenance_url import canonical_page_url

    assert canonical_page_url(f"https://h.example/?{key}=hidden") == "https://h.example/"


@pytest.mark.parametrize("key", ["keyword", "author", "design", "inside", "estate", "barcode"])
def test_canonical_keeps_ordinary_keys_that_contain_secret_words(key):
    """Distinct listing pages must not collapse into one identity and be skipped."""
    from rastro.domain.provenance_url import canonical_page_url

    assert canonical_page_url(f"https://h.example/?{key}=blue") == f"https://h.example/?{key}=blue"


def test_canonical_never_returns_a_bare_query_for_an_unparseable_url():
    from rastro.domain.provenance_url import canonical_page_url

    assert canonical_page_url("https://[broken/?page=2") == ""
