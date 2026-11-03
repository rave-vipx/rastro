"""Run twice with the same database to demonstrate reuse across process restarts."""

import argparse
import json
from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import JsonLdRecipe, Rastro, RecipeContext, RecipeKey, Source, SQLiteRecipeRepository


class Product(BaseModel):
    name: str
    price: Decimal = Field(ge=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="rastro-recipes.db")
    args = parser.parse_args()
    source = Source(url="https://example.com/products", locale="en-US")
    context = RecipeContext(template="product-v1", region="us", schema_revision="1")
    repository = SQLiteRecipeRepository(args.database)
    key = RecipeKey.for_source(source, Product, context)
    recipe = None
    if repository.get(key) is None:
        recipe = JsonLdRecipe(fields={"name": "/name", "price": "/price"})
    # Synthetic input. No website access or AI model is needed.
    html = '<script type="application/ld+json">{"name":"Notebook","price":"12.50"}</script>'
    result = Rastro(repository=repository).extract_html(
        html, source=source, schema=Product, recipe=recipe, context=context
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "recipe_reused": result.recipe_reused,
                "recipe_version": result.recipe_version,
                "records": [record.data.model_dump(mode="json") for record in result.records],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
