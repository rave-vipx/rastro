"""Mutations against the shipped example recipes: some break extraction, some do not (MOCK-002).

Outcomes are observed, not tuned: each case states what the engine actually returns.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from examples.catalogue import RECIPE as CATALOGUE_RECIPE
from examples.catalogue import SOURCE as CATALOGUE_SOURCE
from examples.catalogue import Book
from rastro import Outcome, Rastro, Recipe, Source
from rastro.mocklab.mutations import (
    HtmlChangeClasses,
    HtmlChangeLabel,
    HtmlDuplicateElement,
    HtmlEmptyContainer,
    HtmlRemoveField,
    HtmlReorderChildren,
    HtmlWrapElement,
    MutationPlan,
    MutationStatus,
)

BOOK_PAGE = (
    "<!doctype html><html><body><div class='product_main'>"
    "<h1>Synthetic Book</h1><p class='price_color'>£12.50</p><p class='stock'>In stock</p>"
    "</div></body></html>"
)
PRODUCT_PAGE = (
    "<html><head><script type='application/ld+json'>"
    '{"@context":"https://schema.org","@type":"Product","name":"Trail notebook",'
    '"offers":{"price":"12.50","priceCurrency":"USD"}}'
    "</script></head><body><main><h1>Trail notebook</h1><p>Paper</p></main></body></html>"
)
PRODUCT_RECIPE = Recipe.model_validate_json(
    (Path(__file__).parents[3] / "examples" / "product.recipe.json").read_text()
)


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


def mutate(document: str, *operators) -> str:
    result = MutationPlan(seed=2026, operators=operators).apply(document)
    assert result.status is MutationStatus.APPLIED
    return result.document


@pytest.mark.parametrize(
    ("operator", "outcome", "records"),
    [
        (HtmlReorderChildren(selector=".product_main"), Outcome.SUCCESS, 1),
        (HtmlWrapElement(selector="h1"), Outcome.SUCCESS, 1),
        (HtmlDuplicateElement(selector=".product_main"), Outcome.SUCCESS, 2),
        (HtmlChangeLabel(selector="h1", text="Renamed"), Outcome.SUCCESS, 1),
        (
            HtmlChangeClasses(selector="p", rename={"price_color": "price"}),
            Outcome.INSUFFICIENT_EVIDENCE,
            0,
        ),
        (
            HtmlChangeClasses(selector=".product_main", hash_suffix=True),
            Outcome.MISSING_DATA,
            0,
        ),
        (HtmlRemoveField(selector="h1"), Outcome.INSUFFICIENT_EVIDENCE, 0),
        (HtmlEmptyContainer(selector=".product_main"), Outcome.MISSING_DATA, 0),
    ],
)
def test_catalogue_recipe_under_mutation(operator, outcome, records):
    result = Rastro().extract_html(
        mutate(BOOK_PAGE, operator), source=CATALOGUE_SOURCE, schema=Book, recipe=CATALOGUE_RECIPE
    )
    assert (result.status, len(result.records)) == (outcome, records)


@pytest.mark.parametrize(
    ("operator", "outcome", "records"),
    [
        (HtmlReorderChildren(selector="html"), Outcome.SUCCESS, 1),
        (HtmlWrapElement(selector="main"), Outcome.SUCCESS, 1),
        (HtmlEmptyContainer(selector="main"), Outcome.SUCCESS, 1),
        (HtmlDuplicateElement(selector="script"), Outcome.SUCCESS, 2),
        (HtmlEmptyContainer(selector="script"), Outcome.MISSING_DATA, 0),
        (HtmlRemoveField(selector="script"), Outcome.MISSING_DATA, 0),
    ],
)
def test_product_recipe_under_mutation(operator, outcome, records):
    result = Rastro().extract_html(
        mutate(PRODUCT_PAGE, operator),
        source=Source(url="https://example.com/products", locale="en-US"),
        schema=Product,
        recipe=PRODUCT_RECIPE,
    )
    assert (result.status, len(result.records)) == (outcome, records)
