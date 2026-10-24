# Getting started

## Install from source

Use Python 3.12+ and [uv 0.13.x](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/rave-vipx/rastro.git
cd rastro
uv sync --locked
uv run rastro demo
uv run python examples/offline.py
```

The first installation downloads dependencies; extraction and tests run offline afterward. The core installation does not install browser engines or model runtimes.

For a minimal local library installation in your own environment:

```bash
python -m pip install .
```

The distribution is currently called `rastro-engine`; import it as `rastro`. Registry naming is provisional until release review. Do not assume `pip install rastro` installs this project.

## Define the contract and recipe

```python
from pydantic import BaseModel, Field
from rastro import Rastro, Recipe, Source


class Article(BaseModel):
    title: str = Field(min_length=1)
    author: str | None = None


html = '<article><h1>Local-first data</h1><span class="author">Alex</span></article>'
recipe = Recipe(
    strategy="css",
    record_selector="article",
    fields={"title": "h1::text", "author": ".author::text"},
)
result = Rastro().extract_html(
    html,
    source=Source(url="https://example.com/articles", locale="en-US"),
    schema=Article,
    recipe=recipe,
)
print(result.model_dump_json(indent=2))
```

Supply only HTML you are permitted to process. `Source.url` identifies the origin; no fetch occurs. The synchronous method handles one document. Applications doing asynchronous work should run it in their own bounded worker pool.

## Use structured metadata

JSON-LD recipes select objects by `@type` and map fields with [JSON pointers](https://www.rfc-editor.org/rfc/rfc6901). The parser handles root objects, arrays, and `@graph` containers. It does not fetch contexts or implement JSON-LD expansion.

```python
recipe = Recipe(
    strategy="jsonld",
    item_type="Product",
    fields={"name": "/name", "price": "/offers/price"},
)
```

Object keys containing `/` or `~` use `~1` or `~0`, respectively. Array indices are supported, for example `/offers/0/price`. Omitting `item_type` examines all root/graph objects, so set it when unrelated metadata shares the page.

## Handle outcomes deliberately

Always inspect `result.status` and `result.issues`, including when records are present. `partial_success` means some data was accepted but at least one issue remains. A `success` means the selected candidates passed the supplied schema; it is not a claim about the accuracy or completeness of the website.

See the [result contract](api.md) for details.

## CLI

```bash
uv run rastro --version
uv run rastro doctor
uv run rastro demo
uv run rastro --verbose demo
uv run rastro validate-recipe examples/product.recipe.json
```

Stdout contains JSON, except for help and version. Verbose execution events go to stderr as JSON. Exit `0` indicates success, `1` an extraction result that is not fully successful, and `2` invalid input or usage. `validate-recipe` checks the declarative structure; compatibility with a particular schema and CSS syntax are checked at extraction time.

Fetching URLs and loading arbitrary schema modules from the CLI are not implemented.
