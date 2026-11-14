"""Security audit H1: a job file cannot point Rastro's credentials at a model of its choice."""

import pytest

from rastro.domain.job_providers import CommandProvider, OllamaProvider, OpenAICompatibleProvider
from rastro.domain.model_trust import OperatorTrust, check_job_model
from rastro.domain.models import ConfigurationError

NONE = OperatorTrust()


def openai(**options):
    return OpenAICompatibleProvider(model="m", **options)


@pytest.mark.parametrize("key", ["api_key_env", "base_url_env"])
def test_a_job_cannot_name_an_arbitrary_secret(key):
    options = {key: "AWS_SECRET_ACCESS_KEY"}
    if key == "api_key_env":
        options["base_url"] = "http://127.0.0.1:1234/v1"
    else:
        options["allow_remote"] = True
    with pytest.raises(ConfigurationError) as error:
        check_job_model(openai(**options), OperatorTrust(allow_remote=True))
    assert error.value.location == f"model.{key}"
    assert "AWS_SECRET_ACCESS_KEY" not in str(error.value)
    assert "AWS_SECRET_ACCESS_KEY" not in str(error.value.hint)


def test_operator_namespace_or_allow_list_admits_the_secret_name():
    check_job_model(openai(base_url="http://127.0.0.1:1234/v1", api_key_env="RASTRO_K"), NONE)
    spec = openai(base_url="http://127.0.0.1:1234/v1", api_key_env="OPENAI_API_KEY")
    check_job_model(spec, OperatorTrust(allowed_env=frozenset({"OPENAI_API_KEY"})))


def test_a_remote_endpoint_needs_operator_opt_in():
    spec = openai(base_url="https://attacker.example/v1", allow_remote=True)
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOW_REMOTE=1") as error:
        check_job_model(spec, NONE)
    assert error.value.location == "model"
    check_job_model(spec, OperatorTrust(allow_remote=True))


def test_an_endpoint_from_the_environment_counts_as_remote():
    spec = openai(base_url_env="RASTRO_MODEL_URL", allow_remote=True)
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOW_REMOTE"):
        check_job_model(spec, NONE)


def test_loopback_models_keep_working_without_opt_in():
    check_job_model(OllamaProvider(model="qwen3:4b"), NONE)
    check_job_model(openai(base_url="http://127.0.0.1:1234/v1"), NONE)


def test_a_job_file_can_never_declare_a_command_model():
    spec = CommandProvider(argv=("llm",), local=True)
    everything = OperatorTrust(allow_remote=True, allowed_env=frozenset({"PATH"}))
    for trust in (NONE, everything):
        with pytest.raises(ConfigurationError, match="cannot make Rastro start a program"):
            check_job_model(spec, trust)
