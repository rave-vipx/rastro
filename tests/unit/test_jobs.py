import ipaddress
import json
from typing import Any

import pytest
from pydantic import ValidationError

from rastro import (
    Job,
    ResourceLimitError,
    parse_job,
    schema_fingerprint,
)

COMPLETE_TOML = """
format_version = 1
discover_recipes = true
recipe_memory = "state/recipes.db"

[discovery_aliases]
currency = ["priceCurrency"]

[region]
locale = "es-UY"
languages = ["es-UY", "en"]
timezone = "America/Montevideo"
currency = "UYU"
egress = "proxy_uy"

[context]
template = "catalogue"
region = "uy"
schema_revision = "2"

[limits]
max_records = 50

[acquisition]
allowed_hosts = ["shop.example.com", "2001:db8::1"]
max_concurrency = 2

[acquisition.egress_profiles.proxy_uy]
proxy_url = "https://proxy.example.com:8443"
username_env = "PROXY_USER"
password_env = "PROXY_PASS"

[model]
provider = "openai_compatible"
model = "gpt-4.1-mini"
base_url = "https://api.example.com/v1"
api_key_env = "RASTRO_TEST_KEY"
allow_remote = true
response_format = "json_schema"
token_limit = "max_completion_tokens"

[inference_policy]
max_calls = 2

[[sources]]
url = "https://shop.example.com/products"
representation = "html"

[[sources]]
path = "fixtures/items.json"
representation = "json"
url = "https://shop.example.com/api/items"

[sources.recipe]
strategy = "json"
record_path = "/items"
fields = { name = "/name", price = "/price" }

[[fields]]
name = "name"
type = "string"
min_length = 1
max_length = 120
pattern = "^[A-Za-z].*"

[[fields]]
name = "price"
type = "decimal"
ge = 0
le = 10000

[[fields]]
name = "currency"
type = "string"
required = false
"""


def _job(discover: bool = True, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format_version": 1,
        "sources": [{"path": "a.html"}],
        "fields": [{"name": "x", "type": "string"}],
    }
    if discover:
        base["discover_recipes"] = True
    base.update(overrides)
    return base


def test_toml_and_json_jobs_are_equivalent():
    from_toml = parse_job(COMPLETE_TOML, format="toml")
    from_json = parse_job(from_toml.model_dump_json().encode("utf-8"), format="json")
    assert from_toml == from_json
    assert schema_fingerprint(from_toml.build_schema()) == schema_fingerprint(
        from_json.build_schema()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"sources": [{"path": "a.html"}], "discover_recipes": True, "fields": []},
        {k: v for k, v in _job().items() if k != "format_version"},
        _job(format_version=2),
        _job(format_version=True),
        _job(unknown=1),
        _job(fields=[{"name": "x", "type": "string"}, {"name": "x", "type": "integer"}]),
        _job(sources=[{}]),
        _job(sources=[{"path": "../escape.html"}]),
        _job(sources=[{"path": ":memory:"}]),
        _job(discover=False),
        _job(sources=["https://example.com/a"]),
        _job(sources=["https://sub.example.com/a"], acquisition={"allowed_hosts": ["example.com"]}),
        _job(sources=["http://example.com/a"], acquisition={"allowed_hosts": ["example.com"]}),
        _job(
            sources=["https://example.com/a", "https://example.com/b"],
            acquisition={"allowed_hosts": ["example.com"], "max_sources": 1},
        ),
        _job(region={"geolocation": "0,0"}),
        _job(region={"egress": "corp"}),
        _job(context={"template": "p"}),
        _job(
            discover=False,
            recipe_memory="m.db",
            recipe_version=1,
            recipe={"strategy": "jsonld", "fields": {"x": "/x"}},
        ),
        _job(
            discover=False,
            sources=[{"path": "a.html", "representation": "html"}],
            recipe={"strategy": "json", "record_path": "", "fields": {"x": "/x"}},
        ),
        _job(discover=False, recipe={"strategy": "jsonld", "fields": {"undeclared": "/x"}}),
        _job(
            discover=False,
            sources=[
                {"path": "a.html", "recipe": {"strategy": "jsonld", "fields": {"undeclared": "/x"}}}
            ],
        ),
        _job(discovery_aliases={"other": ["y"]}),
        _job(discovery_aliases={"x": []}),
        _job(
            discover=False,
            model={"provider": "ollama", "model": "qwen3:4b", "base_url": "https://remote.example"},
        ),
        _job(discover=False, model={"provider": "ollama", "model": "qwen-cloud"}),
        _job(
            discover=False,
            model={
                "provider": "ollama",
                "model": "qwen3:4b",
                "base_url": "http://127.0.0.1:11434",
                "base_url_env": "OLLAMA_URL",
            },
        ),
        _job(
            discover=False,
            model={
                "provider": "openai_compatible",
                "model": "m",
                "base_url": "http://remote.example/v1",
                "allow_remote": True,
            },
        ),
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m-cloud", "base_url_env": "U"},
        ),
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m", "temperature": True},
        ),
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m", "base_url": "http://127.0.0.1:0"},
        ),
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m", "base_url": "http://127.0.0.1:x"},
        ),
        _job(
            discover=False,
            model={
                "provider": "openai_compatible",
                "model": "m",
                "base_url": "http://127.0.0.1:11434/v1",
                "base_url_env": "U",
            },
        ),
    ],
)
def test_job_contract_rejects_invalid_definitions(payload):
    with pytest.raises(ValidationError):
        Job.model_validate(payload)


def test_parse_job_rejects_malformed_bom_nonfinite_and_oversized_inputs():
    with pytest.raises(ResourceLimitError):
        parse_job(" " * 262_145, format="json")
    for raw, fmt in [
        ("\ufeff{}", "json"),
        ("[]", "json"),
        ("format_version = 1\nx = nan\n", "toml"),
        ("format_version = 1\nd = 2026-10-30\n", "toml"),
        ("{}", "yaml"),  # type: ignore[arg-type]
    ]:
        with pytest.raises(ValueError):
            parse_job(raw, format=fmt)  # type: ignore[arg-type]
    ipv6 = _job(
        sources=["https://[::1]/x"],
        acquisition={"allowed_hosts": [str(ipaddress.ip_address("::1"))]},
    )
    assert len(Job.model_validate(ipv6).sources) == 1


def test_validation_errors_never_echo_inline_secrets():
    secret = "sk-LIVE-DO-NOT-LEAK-987654"
    for bad in (
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m", "api_key": secret},
        ),
        _job(
            discover=False,
            model={"provider": "openai_compatible", "model": "m", "api_key_env": secret},
        ),
        _job(
            sources=[f"https://user:{secret}@example.com/p"],
            acquisition={"allowed_hosts": ["example.com"]},
        ),
    ):
        with pytest.raises(ValidationError) as exc_info:
            Job.model_validate(bad)
        assert secret not in str(exc_info.value)
        assert secret not in repr(exc_info.value)
        assert secret not in json.dumps(exc_info.value.errors())
