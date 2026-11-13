"""Synthetic CLI smoke demo, kept separate from command dispatch."""

from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import JobExitCode, JsonLdRecipe, Outcome, Rastro, Source


class _DemoProduct(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)


def demo_command() -> int:
    result = Rastro().extract_html(
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Trail notebook","offers":{"price":12.5}}'
        "</script>",
        source=Source(url="https://example.com/products", locale="en-US"),
        schema=_DemoProduct,
        recipe=JsonLdRecipe(
            item_type="Product", fields={"name": "/name", "price": "/offers/price"}
        ),
    )
    print(result.model_dump_json(indent=2))
    return int(JobExitCode.SUCCESS if result.status == Outcome.SUCCESS else JobExitCode.FAILED)
