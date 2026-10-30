import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from rastro import Recipe, Source, schema_fingerprint
from rastro.domain.json import resolve_pointer as _resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "https://user:password@example.com",
        "https://user@example.com",
    ],
)
def test_source_rejects_unsafe_schemes_and_credentials(url):
    with pytest.raises(ValidationError):
        Source(url=url)


def test_source_accepts_explicit_regional_context_and_legacy_defaults():
    source = Source(
        url="https://example.com/",
        locale="es-UY",
        languages=("es-UY", "en"),
        currency="UYU",
        egress="direct",
    )
    assert source.languages == ("es-UY", "en")
    assert source.currency == "UYU"
    legacy = Source.model_validate(
        {"url": "https://example.com/", "locale": "es-UY", "timezone": "UTC"}
    )
    assert legacy.languages == () and legacy.currency is None and legacy.egress == "direct"


@pytest.mark.parametrize(
    "regional",
    [
        {"languages": ("en\r\nX-Injected: yes",)},
        {"languages": ("not_a_language",)},
        {"currency": "usd"},
        {"currency": "US"},
        {"egress": "Proxy"},
        {"egress": "eu proxy"},
        {"egress": ""},
        {"egress": "x" * 65},
        {"geolocation": "-34.9,-56.2"},
    ],
)
def test_source_rejects_invalid_or_unsupported_regional_context(regional):
    with pytest.raises(ValidationError):
        Source(url="https://example.com/", **regional)


@pytest.mark.parametrize(
    "data",
    [
        {"strategy": "python", "fields": {"x": "/x"}},
        {"strategy": "jsonld", "fields": {}},
        {"strategy": "jsonld", "fields": {"x": "x"}},
        {"strategy": "jsonld", "fields": {"x": "/~2"}},
        {"strategy": "jsonld", "fields": {"": "/x"}},
        {"strategy": "jsonld", "fields": {"x": "/x"}, "version": 0},
        {"strategy": "jsonld", "fields": {"x": "/x"}, "code": "print('no')"},
        {"strategy": "jsonld", "fields": {"x": "/x"}, "record_selector": "p"},
        {"strategy": "css", "fields": {"x": "p::text"}},
        {"strategy": "css", "fields": {"x": "p"}, "record_selector": "article"},
        {
            "strategy": "css",
            "fields": {"x": "p::text"},
            "record_selector": "article",
            "item_type": "Product",
        },
    ],
)
def test_recipe_contract_rejects_invalid_or_executable_definitions(data):
    with pytest.raises(ValidationError):
        Recipe.model_validate(data)


def test_recipe_roundtrip(recipe):
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe


def test_schema_fingerprint_is_deterministic_and_sensitive():
    class A(BaseModel):
        name: str

    class B(BaseModel):
        name: int

    assert schema_fingerprint(A) == schema_fingerprint(A)
    assert schema_fingerprint(A) != schema_fingerprint(B)
    assert len(schema_fingerprint(A)) == 64


@given(st.text(max_size=50), st.integers())
def test_json_pointer_handles_arbitrary_keys(key, value):
    pointer = "/" + key.replace("~", "~0").replace("/", "~1")
    assert _resolve({key: value}, pointer) == value


@pytest.mark.parametrize("pointer", ["/1", "/00", "/-1", "/999999999999", "/a", "/\u0661"])
def test_json_pointer_does_not_guess_indices(pointer):
    assert _resolve(["value"], pointer) != "value"


def test_nested_json_pointer():
    assert _resolve({"offers": [{"price": 12}]}, "/offers/0/price") == 12
    assert _resolve({"x": 1}, "/x/y") != 1


@given(st.dictionaries(st.text(min_size=1, max_size=10), st.integers(), min_size=1, max_size=10))
def test_recipe_serialization_is_declarative(fields):
    mapping = {name: "/" + name.replace("~", "~0").replace("/", "~1") for name in fields}
    recipe = Recipe(strategy="jsonld", fields=mapping)
    assert json.loads(recipe.model_dump_json())["fields"] == mapping
