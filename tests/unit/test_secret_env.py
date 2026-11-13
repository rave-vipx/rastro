"""A job file may only make Rastro read operator-approved env variables (audit H1)."""

import pytest

from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name


@pytest.mark.parametrize("name", ["RASTRO_PROXY_USER", "RASTRO_X", "RASTRO_" + "A" * 64])
def test_rastro_prefixed_names_are_accepted(name: str) -> None:
    assert check_secret_env_name(name, frozenset()) == name


@pytest.mark.parametrize(
    "name",
    ["GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "USER", "RASTRO_", "rastro_x", "RASTRO_" + "A" * 65],
)
def test_other_names_need_the_operator_allow_list(name: str) -> None:
    with pytest.raises(ConfigurationError, match="RASTRO_ALLOWED_ENV"):
        check_secret_env_name(name, frozenset())


def test_refusal_does_not_echo_the_name() -> None:
    with pytest.raises(ConfigurationError) as caught:
        check_secret_env_name("GITHUB_TOKEN", frozenset())
    assert "GITHUB" not in str(caught.value)


def test_operator_allow_list_admits_a_named_variable() -> None:
    assert check_secret_env_name("PROXY_USER", frozenset({"PROXY_USER"})) == "PROXY_USER"
