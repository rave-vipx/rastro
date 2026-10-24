from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from rastro import Recipe, Source


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = "USD"


@pytest.fixture
def schema() -> type[Product]:
    return Product


@pytest.fixture
def source() -> Source:
    return Source(url="https://example.com/products?token=private#secret", locale="es-UY")


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(
        strategy="jsonld", item_type="Product", fields={"name": "/name", "price": "/offers/price"}
    )


@pytest.fixture
def html() -> str:
    return (Path(__file__).parent / "fixtures" / "products.html").read_text()
