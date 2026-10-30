import pytest
from pydantic import ValidationError

from rastro import AcquisitionPolicy, Source
from rastro.domain.acquisition import EgressProfile, ProxyRoute
from rastro.domain.memory import RecipeKey


def policy(profiles):
    return AcquisitionPolicy(allowed_hosts=("example.com",), egress_profiles=profiles)


def test_named_profiles_accept_shorthand_and_normalize_endpoints():
    configured = policy(
        {
            "eu-office": "HTTP://Proxy.Example.:3128/",
            "relay_6": {
                "proxy_url": "https://[2001:db8::1]",
                "username_env": "RASTRO_PROXY_USER",
                "password_env": "RASTRO_PROXY_PASSWORD",
            },
        }
    )
    assert configured.egress_profiles["eu-office"] == EgressProfile(
        proxy_url="http://proxy.example:3128"
    )
    assert configured.egress_profiles["relay_6"].proxy_url == "https://[2001:db8::1]"
    assert AcquisitionPolicy(allowed_hosts=("example.com",)).egress_profiles == {}


@pytest.mark.parametrize(
    "url",
    [
        "socks5://proxy.example:1080",
        "socks5h://proxy.example:1080",
        "ftp://proxy.example",
        "proxy.example:3128",
        "http://user:secret@proxy.example:3128",
        "http://proxy.example:3128/path",
        "http://proxy.example:3128?x=1",
        "http://proxy.example:3128#x",
        "http://proxy.example:99999",
        "http://proxy.example:3128\r\nX: 1",
        "http:// proxy.example",
        "http://proxy_example",
        "http://",
    ],
)
def test_proxy_endpoint_must_be_an_explicit_http_or_https_origin(url):
    with pytest.raises(ValidationError):
        policy({"office": url})


@pytest.mark.parametrize(
    "profiles",
    [
        ["http://proxy.example:3128"],
        {"direct": "http://proxy.example:3128"},
        {"Office": "http://proxy.example:3128"},
        {"bad name": "http://proxy.example:3128"},
        {f"p{index}": "http://proxy.example:3128" for index in range(17)},
        {"office": {"proxy_url": "http://proxy.example", "password_env": "PASSWORD"}},
        {"office": {"proxy_url": "http://proxy.example", "username_env": "BAD-NAME"}},
        {"office": {"proxy_url": "http://proxy.example", "username": "inline"}},
    ],
)
def test_profile_names_and_secret_references_are_validated(profiles):
    with pytest.raises(ValidationError):
        policy(profiles)


def test_policy_serializes_secret_references_not_values():
    configured = policy(
        {"office": {"proxy_url": "http://proxy.example", "username_env": "PROXY_USER"}}
    )
    assert configured.model_dump()["egress_profiles"]["office"] == {
        "proxy_url": "http://proxy.example",
        "username_env": "PROXY_USER",
        "password_env": None,
    }


def test_proxy_route_repr_never_contains_credentials():
    route = ProxyRoute("office", "http://proxy.example", "user-value", "password-value")
    assert "user-value" not in repr(route) and "password-value" not in repr(route)


def test_source_selects_egress_by_name_and_recipe_keys_isolate_it(schema):
    direct = Source(url="https://example.com/")
    proxied = Source(url="https://example.com/", egress="eu-office")
    assert proxied.egress == "eu-office"
    direct_key = RecipeKey.for_source(direct, schema)
    proxied_key = RecipeKey.for_source(proxied, schema)
    assert '"egress"' not in direct_key.canonical()
    assert '"egress":"eu-office"' in proxied_key.canonical()
    assert direct_key.canonical() != proxied_key.canonical()
    assert "proxy.example" not in proxied_key.canonical()
