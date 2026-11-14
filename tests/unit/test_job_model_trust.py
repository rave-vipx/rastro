"""Security audit H1: a job file cannot point Rastro's credentials at a model of its choice."""

import pytest

from rastro.application.job_loader import check_job_model
from rastro.domain.job_providers import OllamaProvider, OpenAICompatibleProvider
from rastro.domain.models import ConfigurationError


def openai(**options):
    return OpenAICompatibleProvider(model="m", **options)


def test_a_job_cannot_name_an_arbitrary_secret_for_the_model_key():
    spec = openai(base_url="http://127.0.0.1:18999/v1", api_key_env="AWS_SECRET_ACCESS_KEY")
    with pytest.raises(ConfigurationError) as error:
        check_job_model(spec, environ={})
    assert error.value.location == "model.api_key_env"
    assert "AWS_SECRET_ACCESS_KEY" not in str(error.value)


def test_operator_namespace_or_allow_list_admits_the_secret_name():
    check_job_model(openai(api_key_env="RASTRO_MODEL_KEY"), environ={})
    spec = openai(api_key_env="OPENAI_API_KEY")
    check_job_model(spec, environ={"RASTRO_ALLOWED_ENV": "OPENAI_API_KEY"})


def test_a_remote_endpoint_needs_operator_opt_in():
    spec = openai(base_url="https://attacker.example/v1", allow_remote=True)
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOW_REMOTE=1") as error:
        check_job_model(spec, environ={})
    assert error.value.location == "model"
    check_job_model(spec, environ={"RASTRO_ALLOW_REMOTE": "1"})


def test_an_endpoint_from_the_environment_counts_as_remote():
    spec = openai(base_url_env="RASTRO_MODEL_URL", allow_remote=True)
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOW_REMOTE"):
        check_job_model(spec, environ={})


def test_loopback_models_keep_working_without_opt_in():
    check_job_model(OllamaProvider(model="qwen3:4b"), environ={})
    check_job_model(openai(base_url="http://127.0.0.1:1234/v1"), environ={})
