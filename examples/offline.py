"""Run with `uv run python examples/offline.py`; all data is synthetic."""

from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import Outcome, Rastro, Recipe, Source


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


html = """
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Trail notebook",
  "offers": {"price": "12.50", "priceCurrency": "USD"}
}
</script>
"""
recipe = Recipe(
    strategy="jsonld",
    item_type="Product",
    fields={"name": "/name", "price": "/offers/price", "currency": "/offers/priceCurrency"},
)
result = Rastro().extract_html(
    html,
    source=Source(url="https://example.com/products", locale="en-US"),
    schema=Product,
    recipe=recipe,
)
assert result.status == Outcome.SUCCESS
print(result.model_dump_json(indent=2))
