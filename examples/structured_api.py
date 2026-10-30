"""Extract a synthetic JSON API response; run twice with one database to see reuse.

Everything is offline: supplied JSON text, then the HTTP JSON path through an
in-process transport that returns synthetic responses. No website or model is used.
"""

import argparse
import asyncio
import json
from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import (
    AcquisitionPolicy,
    ExtractionResult,
    JsonRecipe,
    Outcome,
    Rastro,
    Recipe,
    RecipeContext,
    Source,
    SQLiteRecipeRepository,
)

API = json.dumps(
    {
        "data": {
            "products": [
                {"name": "Notebook", "offer": {"price": "12.50"}},
                {"name": "Pen", "offer": {"price": "1.20"}},
            ]
        },
        "meta": {"page": 1},
    }
)


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)


async def acquire_over_http(source: Source, recipe: Recipe) -> dict[str, object]:
    try:
        from rastro.application.acquisition import WebAcquirer
        from rastro.domain.acquisition import HttpResponse, PinnedTarget, ProxyRoute
    except ModuleNotFoundError:
        return {"skipped": "install rastro-engine[http]"}

    class Resolver:
        async def resolve(self, host: str, port: int) -> tuple[str, ...]:
            return ("93.184.215.14",)

    class Transport:
        """Synthetic responses: no robots.txt, then a JSON body or an HTML page."""

        def __init__(self, content_type: str) -> None:
            self.content_type = content_type

        async def get(
            self,
            target: PinnedTarget,
            *,
            headers: dict[str, str],
            timeout: float,
            max_bytes: int,
            proxy: ProxyRoute | None = None,
        ) -> HttpResponse:
            if target.url.endswith("/robots.txt"):
                return HttpResponse(404, {"content-type": "text/plain"}, b"", 0)
            body = API.encode()
            return HttpResponse(200, {"content-type": self.content_type}, body, len(body))

    async def run(content_type: str) -> ExtractionResult[Product]:
        policy = AcquisitionPolicy(allowed_hosts=("api.example.com",), min_interval_seconds=0)
        acquirer = WebAcquirer(policy, transport=Transport(content_type), resolver=Resolver())
        return await Rastro(acquisition=acquirer, policy=policy).extract(
            source=source, schema=Product, recipe=recipe, representation="json"
        )

    accepted = await run("application/json")
    # A JSON request that receives HTML is a permanent failure, never parsed as HTML.
    rejected = await run("text/html")
    return {
        "status": accepted.status.value,
        "accepted": accepted.metrics.accepted,
        "rejected_content_type": rejected.status == Outcome.PERMANENT_FAILURE,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="rastro-recipes.db")
    args = parser.parse_args()
    source = Source(url="https://api.example.com/v1/products", locale="en-US")
    recipe = JsonRecipe(
        record_path="/data/products", fields={"name": "/name", "price": "/offer/price"}
    )
    explicit = Rastro().extract_json(API, source=source, schema=Product, recipe=recipe)
    # The first run discovers exact field-name matches; later runs reuse validated memory.
    discovered = await Rastro(
        repository=SQLiteRecipeRepository(args.database), discover_recipes=True
    ).extract_json_async(
        API, source=source, schema=Product, context=RecipeContext(template="products-v1")
    )
    print(
        json.dumps(
            {
                "explicit": {
                    "status": explicit.status.value,
                    "evidence": {
                        name: evidence.locator
                        for name, evidence in explicit.records[0].evidence.items()
                    },
                },
                "discovered": {
                    "status": discovered.status.value,
                    "recipe_reused": discovered.recipe_reused,
                    "learned_recipe": (
                        discovered.learned_recipe.model_dump(mode="json", exclude_defaults=True)
                        if discovered.learned_recipe
                        else None
                    ),
                    "records": [r.data.model_dump(mode="json") for r in discovered.records],
                },
                "http": await acquire_over_http(source, recipe),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
