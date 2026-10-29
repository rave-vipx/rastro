"""Discover synthetic JSON-LD offline, or opt in to an already-installed Ollama model."""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import InferencePolicy, ModelPort, Outcome, Rastro, Source, SQLiteRecipeRepository


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)


async def run(model_name: str | None, database: str | None) -> int:
    model: ModelPort | None = None
    if model_name:
        from rastro.adapters.ollama import OllamaModel

        model = OllamaModel(model_name)
        html = '<article class="product"><h2>Notebook</h2><b class="price">12.50</b></article>'
        source = Source(url="https://example.com/model-example")
    else:
        html = '<script type="application/ld+json">{"name":"Notebook","price":"12.50"}</script>'
        source = Source(url="https://example.com/discovery-example")
    result = await Rastro(
        discover_recipes=True,
        model=model,
        inference_policy=InferencePolicy(max_calls=2, max_repair_attempts=1),
        repository=SQLiteRecipeRepository(database) if database else None,
    ).extract_html_async(html, source=source, schema=Product)
    print(
        json.dumps(
            {
                "status": result.status.value,
                "recipe_reused": result.recipe_reused,
                "inference_calls": result.metrics.inference_calls,
                "learned_recipe": result.learned_recipe.model_dump()
                if result.learned_recipe
                else None,
                "records": [record.data.model_dump(mode="json") for record in result.records],
                "issues": [issue.code for issue in result.issues],
            },
            indent=2,
        )
    )
    return 0 if result.status == Outcome.SUCCESS else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", help="An installed local Ollama model; requires the ollama extra"
    )
    parser.add_argument("--database", help="Optional SQLite memory path")
    args = parser.parse_args()
    return asyncio.run(run(args.model, args.database))


if __name__ == "__main__":
    raise SystemExit(main())
