"""Rebuild the synthetic registry with the normal export and publish gates.

Run from the checkout: uv run python examples/registry/build.py /tmp/new-registry
The destination must be new: published package versions are immutable.
"""

import argparse
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.structured import JsonParser
from rastro.application.recipe_hub import export_bundle
from rastro.application.recipe_registry import publish_to_registry
from rastro.domain.job_fields import StringField
from rastro.domain.jobs import Job
from rastro.domain.memory import RecipeContext
from rastro.domain.models import CssRecipe, Representation, Source
from rastro.domain.recipe_bundle import BundleAuthor, canonical_json
from rastro.domain.recipes import JsonRecipe, Recipe
from rastro.ports.parser import DocumentParser

VERIFIED_ON = date(2026, 11, 16)
SAMPLES = {
    "listing": (
        '<ul><li class="card"><h2>Example workshop</h2><p>North room</p></li>'
        '<li class="card"><h2>Example meetup</h2><p>South room</p></li></ul>\n',
        "location",
    ),
    "news": (
        '<main><article class="card"><h2>Example library opens</h2><p>Culture</p></article>'
        '<article class="card"><h2>Example garden grows</h2><p>Community</p></article></main>\n',
        "section",
    ),
    "product": (
        '<main><article class="card"><h2>Example notebook</h2><p>12.50</p></article>'
        '<article class="card"><h2>Example pencil</h2><p>2.00</p></article></main>\n',
        "price_text",
    ),
}

PAGE_FIELDS = (StringField(name="title", min_length=1), StringField(name="summary", min_length=1))
PRODUCT_FIELDS = (StringField(name="title", min_length=1), StringField(name="price", min_length=1))
PAGE_RECIPE = CssRecipe(
    record_selector=".page",
    fields={"title": "h1::text", "summary": "p::text"},
    require_evidence=True,
)
# Starter recipes for crawling: id -> (recipe, fields, sample file, source URL, spider table).
STARTERS: dict[str, tuple[Recipe, tuple[StringField, ...], str, str, dict[str, object]]] = {
    "sitemap-crawl": (
        PAGE_RECIPE,
        PAGE_FIELDS,
        '<main class="page"><h1>Example opening hours</h1><p>Open every day.</p></main>\n',
        "https://sitemap-crawl.example/sitemap.xml",
        {"mode": "spider", "seed": "sitemap", "max_pages": 25, "deny_pattern": "/(login|cart)"},
    ),
    "shopify-products": (
        JsonRecipe(
            record_path="/products",
            fields={"title": "/title", "price": "/variants/0/price"},
            require_evidence=True,
        ),
        PRODUCT_FIELDS,
        '{"products": [{"title": "Example kettle", "variants": [{"price": "24.00"}]},'
        ' {"title": "Example mug", "variants": [{"price": "6.50"}]}]}\n',
        "https://shopify-products.example/products.json",
        {},
    ),
    "generic-crawl": (
        PAGE_RECIPE,
        PAGE_FIELDS,
        '<main class="page"><h1>Example home</h1><p>Welcome.</p>'
        '<a href="/about">About</a></main>\n',
        "https://generic-crawl.example/",
        {"mode": "spider", "links": "a::attr(href)", "max_depth": 2, "max_pages": 25},
    ),
}


def parser_for(representation: Representation) -> DocumentParser:
    return JsonParser() if representation == "json" else ScraplingParser()


def build(registry: Path) -> None:
    # Keep the original synthetic agenda package and its published verification date.
    publish_to_registry(
        Path(__file__).parent / "recipes/teatro-central-agenda/1.0.0",
        registry,
        parser_for,
        today=date(2026, 11, 15),
    )
    for kind, (html, detail) in SAMPLES.items():
        with TemporaryDirectory() as scratch:
            sample = Path(scratch) / "sample.html"
            sample.write_bytes(html.encode("utf-8"))
            fields = (
                StringField(name="title", min_length=1),
                StringField(name=detail, min_length=1),
            )
            bundle = Path(scratch) / "bundle"
            export_bundle(
                bundle,
                recipe=CssRecipe(
                    record_selector=".card",
                    fields={"title": "h2::text", detail: "p::text"},
                    require_evidence=True,
                ),
                source=Source(url=f"https://{kind}.example/cards"),
                context=RecipeContext(),
                fields=fields,
                sample=sample,
                parser=ScraplingParser(),
                verified_on=VERIFIED_ON,
                bundle_id=f"synthetic-{kind}",
                version="1.0.0",
                author=BundleAuthor(name="Rastro Contributors"),
                license="Apache-2.0",
                public=True,
            )
            publish_to_registry(bundle, registry, parser_for, today=VERIFIED_ON)
            job = {
                "format_version": 1,
                "recipe_memory": f"{kind}-recipes.sqlite",
                "fields": [field.model_dump(mode="json") for field in fields],
                "sources": [
                    {
                        "kind": "file",
                        "path": f"recipes/synthetic-{kind}/1.0.0/sample.html",
                        "url": f"https://{kind}.example/cards",
                    }
                ],
                "dataset": {"name": kind, "path": f"{kind}-data.sqlite", "identity": ["title"]},
            }
            (registry / f"{kind}.json").write_bytes(canonical_json(job))

    for starter_id, starter in STARTERS.items():
        publish_starter(registry, starter_id, *starter)


def publish_starter(
    registry: Path,
    starter_id: str,
    recipe: Recipe,
    fields: tuple[StringField, ...],
    sample_text: str,
    url: str,
    spider: dict[str, object],
) -> None:
    """Publish one crawl starter and write the job template that applies it."""
    json_sample = recipe.representation == "json"
    with TemporaryDirectory() as scratch:
        sample = Path(scratch) / ("sample.json" if json_sample else "sample.html")
        sample.write_bytes(sample_text.encode("utf-8"))
        bundle = Path(scratch) / "bundle"
        export_bundle(
            bundle,
            recipe=recipe,
            source=Source(url=url),
            context=RecipeContext(),
            fields=fields,
            sample=sample,
            parser=parser_for(recipe.representation),
            verified_on=VERIFIED_ON,
            bundle_id=starter_id,
            version="1.0.0",
            author=BundleAuthor(name="Rastro Contributors"),
            license="Apache-2.0",
            public=True,
        )
        publish_to_registry(bundle, registry, parser_for, today=VERIFIED_ON)
    job = {
        "format_version": 1,
        "recipe_memory": f"{starter_id}-recipes.sqlite",
        "fields": [field.model_dump(mode="json") for field in fields],
        "sources": [{"url": url, "representation": recipe.representation}],
        "acquisition": {"allowed_hosts": [url.split("/")[2]]},
        "dataset": {
            "name": starter_id.replace("-", "_"),
            "path": f"{starter_id}-data.sqlite",
            "identity": ["title"],
        },
    }
    if spider:
        job["spider"] = spider
    Job.model_validate(job)
    (registry / f"{starter_id}.json").write_bytes(canonical_json(job))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)
