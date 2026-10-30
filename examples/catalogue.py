"""Fetch one public scraping-demo page; run twice to verify persistent recipe reuse."""

import argparse
import asyncio
import re
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator

from rastro import (
    AcquisitionPolicy,
    Outcome,
    Rastro,
    RastroError,
    Recipe,
    RecipeContext,
    RecipeKey,
    Source,
    SQLiteRecipeRepository,
)

SOURCE = Source(
    url="https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    locale="en-GB",
    languages=("en-GB", "en"),
    timezone="Europe/London",
    currency="GBP",
    egress="direct",
)
CONTEXT = RecipeContext(template="books-demo-product-v1", region="gb", schema_revision="1")
RECIPE = Recipe(
    strategy="css",
    record_selector=".product_main",
    fields={"name": "h1::text", "price_gbp": ".price_color::text"},
    require_evidence=True,
)


class Book(BaseModel):
    name: str = Field(min_length=1)
    price_gbp: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("price_gbp", mode="before")
    @classmethod
    def explicit_pounds(cls, value: object, info: ValidationInfo) -> object:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"£[0-9]+\.[0-9]{2}", value) is None
            or not info.context
            or info.context.get("currency") != "GBP"
        ):
            raise ValueError("Expected a GBP price with two decimal places")
        return value[1:]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="rastro-catalogue.db")
    args = parser.parse_args()
    try:
        repository = SQLiteRecipeRepository(args.database)
        key = RecipeKey.for_source(SOURCE, Book, CONTEXT)
        recipe = RECIPE if repository.get(key) is None else None
        engine = Rastro.web(
            AcquisitionPolicy(allowed_hosts=("books.toscrape.com",)), repository=repository
        )
        result = await engine.extract(source=SOURCE, schema=Book, recipe=recipe, context=CONTEXT)
    except (ValidationError, RastroError):
        parser.exit(2, "Invalid configuration. Check the HTTP extra and recipe database.\n")
    print(result.model_dump_json(indent=2))
    return 0 if result.status == Outcome.SUCCESS else 1


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        raise SystemExit(runner.run(main()))
