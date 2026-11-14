"""Strict scalar parsing and env-reference rules for declarative jobs (JOB-002)."""

from typing import Any

import pytest
from pydantic import ValidationError

from rastro import Job, OpenAICompatibleProvider, parse_job


def _job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format_version": 1,
        "discover_recipes": True,
        "sources": [{"path": "a.html"}],
        "fields": [{"name": "x", "type": "string"}],
    }
    base.update(overrides)
    return base


def _acq(**policy: Any) -> dict[str, Any]:
    return _job(
        sources=["https://shop.example.com/"],
        acquisition={"allowed_hosts": ["shop.example.com"], **policy},
    )


@pytest.mark.parametrize(
    "payload",
    [
        _acq(allow_http="yes"),
        _acq(allow_http=1),
        _acq(max_redirects="3"),
        _acq(max_redirects=3.0),
        _acq(max_concurrency=True),
        _acq(min_interval_seconds="0.5"),
        _acq(egress_profiles={"p": {"proxy_url": "https://proxy.example.com", "username_env": 1}}),
        _job(limits={"max_records": "5"}),
        _job(inference_policy={"max_calls": "2"}),
        _job(fields=[{"name": "x", "type": "number", "ge": "1"}]),
        _job(fields=[{"name": "x", "type": "integer", "le": 1.0}]),
        _job(fields=[{"name": "x", "type": "decimal", "ge": True}]),
        _job(fields=[{"name": "x", "type": "string", "min_length": "1"}]),
        _job(discover_recipes="true"),
        _job(model={"provider": "openai_compatible", "model": "m", "temperature": "0.5"}),
    ],
)
def test_lax_scalar_coercion_is_rejected(payload):
    with pytest.raises(ValidationError):
        Job.model_validate(payload)


def test_strict_numbers_still_accept_json_numbers_and_decimal_strings():
    job = Job.model_validate(
        _acq(
            min_interval_seconds=1,
            max_redirects=3,
            allow_http=False,
        )
        | {
            "fields": [
                {"name": "x", "type": "number", "ge": 1, "le": 2.5},
                {"name": "y", "type": "decimal", "ge": "0.01", "le": 10},
            ]
        }
    )
    assert job.acquisition is not None and job.acquisition.min_interval_seconds == 1.0
    toml = parse_job(
        'format_version = 1\ndiscover_recipes = true\n[[sources]]\npath = "a.html"\n'
        '[[fields]]\nname = "x"\ntype = "number"\nge = 0\n',
        format="toml",
    )
    assert toml.fields[0].ge == 0.0  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "name", ["ghp_AbCdEfGh0123456789", "lower", "Mixed_Case", "1LEADING", "WITH-DASH"]
)
def test_env_references_must_be_uppercase(name):
    with pytest.raises(ValidationError):
        Job.model_validate(
            _job(model={"provider": "openai_compatible", "model": "m", "api_key_env": name})
        )
    with pytest.raises(ValidationError):
        Job.model_validate(
            _acq(
                egress_profiles={
                    "p": {
                        "proxy_url": "https://proxy.example.com",
                        "username_env": "RASTRO_PROXY_USER",
                        "password_env": name,
                    }
                }
            )
        )
    with pytest.raises(ValidationError):
        Job.model_validate(_job(model={"provider": "ollama", "model": "m", "base_url_env": name}))


def test_base_url_env_requires_explicit_remote_opt_in():
    with pytest.raises(ValidationError, match="allow_remote"):
        OpenAICompatibleProvider(model="m", base_url_env="MODEL_URL")
    assert OpenAICompatibleProvider(model="m", base_url_env="MODEL_URL", allow_remote=True)


def test_redacted_dump_hides_every_env_reference():
    job = Job.model_validate(
        _acq(
            egress_profiles={
                "p": {
                    "proxy_url": "https://proxy.example.com",
                    "username_env": "PROXY_USER",
                    "password_env": "PROXY_PASS",
                }
            }
        )
        | {
            "model": {
                "provider": "openai_compatible",
                "model": "m",
                "api_key_env": "API_KEY",
                "base_url_env": "BASE",
                "allow_remote": True,
            }
        }
    )
    dumped = job.redacted_dump()
    profile = dumped["acquisition"]["egress_profiles"]["p"]
    assert profile["username_env"] == profile["password_env"] == "<redacted>"
    assert dumped["model"]["api_key_env"] == dumped["model"]["base_url_env"] == "<redacted>"
    assert dumped["acquisition"]["allowed_hosts"] == ["shop.example.com"]
