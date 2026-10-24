"""Fetch a product from an explicitly permitted host with the asynchronous SDK."""

import argparse
import asyncio
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError

from rastro import AcquisitionPolicy, Outcome, Rastro, RastroError, Recipe, Source


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--allow-host", action="append", required=True)
    parser.add_argument("--locale", default="und")
    args = parser.parse_args()
    try:
        engine = Rastro.web(AcquisitionPolicy(allowed_hosts=tuple(args.allow_host)))
        result = await engine.extract(
            source=Source(url=args.url, locale=args.locale),
            schema=Product,
            recipe=Recipe(
                strategy="jsonld",
                item_type="Product",
                fields={"name": "/name", "price": "/offers/price"},
            ),
        )
    except (ValidationError, RastroError):
        parser.exit(2, "Invalid configuration. Check the URL, permitted hosts, and HTTP extra.\n")
    print(result.model_dump_json(indent=2))
    return 0 if result.status == Outcome.SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
