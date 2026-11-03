import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, Field

from rastro import ConfigurationError
from rastro.application.field_aliases import discovery_aliases


class Aliased(BaseModel):
    currency: str = Field(alias="priceCurrency")
    amount: float = Field(validation_alias=AliasChoices("price", "cost"))
    nested: str = Field(validation_alias=AliasPath("offer", "text"))
    plain: str


def test_declared_aliases_exclude_the_field_name_and_ignore_alias_paths():
    assert discovery_aliases(Aliased.model_fields) == {
        "currency": ("priceCurrency",),
        "amount": ("price", "cost"),
    }


def test_explicit_map_merges_with_declared_aliases_without_duplicates():
    assert discovery_aliases(Aliased.model_fields, {"currency": ("priceCurrency", "value")}) == {
        "currency": ("priceCurrency", "value"),
        "amount": ("price", "cost"),
    }


def test_unknown_or_malformed_explicit_aliases_are_configuration_errors():
    with pytest.raises(ConfigurationError, match="undeclared"):
        discovery_aliases(Aliased.model_fields, {"missing": ("value",)})
    with pytest.raises(ConfigurationError, match="sequences"):
        discovery_aliases(Aliased.model_fields, {"plain": 1})  # type: ignore[dict-item]


def test_alias_count_and_length_are_bounded():
    with pytest.raises(ConfigurationError, match="bounded"):
        discovery_aliases(
            Aliased.model_fields, {"plain": tuple(f"a{index}" for index in range(17))}
        )
    with pytest.raises(ConfigurationError, match="bounded"):
        discovery_aliases(Aliased.model_fields, {"plain": ("x" * 257,)})
