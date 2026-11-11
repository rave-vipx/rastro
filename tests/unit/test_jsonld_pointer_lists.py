"""SCR-*: a JSON-LD field may list pointers; the first that resolves wins."""

import pytest
from pydantic import ValidationError

from rastro.adapters.scrapling import ScraplingParser
from rastro.domain.models import JsonLdRecipe, Limits

PAGE = (
    '<script type="application/ld+json">'
    '{"@type": "Product", "name": "Notebook", "offers": {"lowPrice": "9"}}</script>'
)


def _parse(fields):
    recipe = JsonLdRecipe(item_type="Product", fields=fields)
    return ScraplingParser().parse(PAGE, recipe, Limits()).candidates[0]


def test_first_pointer_wins_and_is_recorded() -> None:
    candidate = _parse({"price": ["/offers/lowPrice", "/offers/price"]})
    assert candidate.values["price"] == "9"
    assert candidate.locators["price"].endswith("/offers/lowPrice")


def test_second_pointer_used_when_first_is_missing() -> None:
    candidate = _parse({"price": ["/offers/price", "/offers/lowPrice"], "name": "/name"})
    assert candidate.values == {"price": "9", "name": "Notebook"}
    assert candidate.locators["price"].endswith("/offers/lowPrice")


def test_no_pointer_present_leaves_the_field_missing() -> None:
    assert "price" not in _parse({"price": ["/a", "/b"], "name": "/name"}).values


@pytest.mark.parametrize(
    "mapping", [[], ["/a"] * 5, ["name"], ["/a", ""], ["/a", "bad~2"]], ids=str
)
def test_invalid_pointer_lists_are_rejected(mapping) -> None:
    with pytest.raises(ValidationError):
        JsonLdRecipe(fields={"price": mapping})


def test_pointer_list_round_trips_through_json() -> None:
    recipe = JsonLdRecipe(fields={"price": ["/a", "/b"]})
    assert JsonLdRecipe.model_validate_json(recipe.model_dump_json()) == recipe
