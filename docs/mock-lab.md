# Instant Mock Lab: fixtures

Mock Lab turns a successful extraction into a reproducible, labeled and sanitized test fixture.
Everything runs offline, and the core install needs no extra dependency. Design rationale is in
[ADR 0014](decisions/0014-mock-lab.md).

## Generate a fixture

```python
from decimal import Decimal

from pydantic import BaseModel, Field

from rastro import CssRecipe, Rastro, Source
from rastro.mocklab import build_fixture, load_fixture, verify_fixture


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0)


html = """
<article class="product"><h2>Notebook</h2><span data-price="12.50"></span></article>
<p>Questions? Write to support@example.com</p>
"""
source = Source(url="https://shop.example.com/products?session=abc")
recipe = CssRecipe(
    record_selector="article.product",
    fields={"name": "h2::text", "price": "span::attr(data-price)"},
)
result = Rastro().extract_html(html, source=source, schema=Product, recipe=recipe)

bundle = build_fixture(
    html,
    source=source,
    schema=Product,
    recipe=recipe,
    result=result,
    seed=7,
    headers={"Content-Type": "text/html", "Set-Cookie": "sid=..."},
)
bundle.write("tests/fixtures/shop-products")

# In a test: load the bundle and prove it still reproduces the extraction.
verify_fixture(load_fixture("tests/fixtures/shop-products"), Product)
```

`build_fixture` needs a `SUCCESS` or `PARTIAL_SUCCESS` result produced with the same schema and
recipe. Before returning, it replays the sanitized body through Rastro and raises
`FixtureMismatchError` if the result differs. For JSON responses, pass the response text and a
`JsonRecipe`; the root must be a JSON object so the body can carry its label.

## What is written

| File | Contents |
| --- | --- |
| `manifest.json` | `format_version`, synthetic label, seed, source without query, recipe, schema fingerprint, allowlisted headers, file digests |
| `body.html` / `body.json` | Sanitized body. HTML starts with `<!-- rastro-synthetic {...} -->`; JSON has a `"__rastro_synthetic__"` key |
| `expected.json` | Expected status, record values, evidence locators, issues and counts |
| `records.json` | Schema-valid synthetic records (`synthetic_count`, default 3) |

Equal inputs and seed produce byte-identical files. A different seed changes the placeholder text
and synthetic records but not the expected extraction.

## What sanitization removes

Sanitization is always on, and nothing can turn it off. A fixture keeps page structure and the
values the recipe extracts. Everything else is replaced or dropped:

- Text outside extracted values becomes placeholder text of the same length.
- Comments, scripts and styles are removed. JSON-LD is kept, scrubbed, only when a recipe reads it.
- `password` and `hidden` input values, event handlers and inline styles are dropped. Other
  attribute values become placeholders unless they are structural (`class`, `id`, `name`,
  `type`...) or quoted in the recipe's selectors.
- URLs lose credentials, query strings and fragments. `javascript:`, `mailto:` and `data:` become
  `#`.
- JSON values outside extracted pointers become placeholders or small integers.
- Response headers other than `content-type` and `content-language` are discarded.

Fixture generation fails with `SanitizationError` when an extracted value, a retained attribute or
an allowlisted header looks like a credential, token, email address or phone number, and when any
finished file still matches one of those patterns. The error names the field or file and the
category, never the value.

## Limitations

- Selectors that depend on removed attribute values, such as query strings, fail the replay check.
  Use structural selectors.
- Hostnames and JSON key names are kept.
- Synthetic records fall back to the fixture's own expected values for fields with patterns,
  nested types or model validators, and list them in `resampled_fields`.
