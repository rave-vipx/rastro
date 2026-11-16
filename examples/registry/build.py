"""Rebuild the synthetic N6 registry with the normal export and publish gates.

Run from the checkout: uv run python examples/registry/build.py /tmp/new-registry
The destination must be new: published package versions are immutable.
"""

import argparse
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rastro.adapters.scrapling import ScraplingParser
from rastro.application.recipe_hub import export_bundle
from rastro.application.recipe_registry import publish_to_registry
from rastro.domain.job_fields import StringField
from rastro.domain.memory import RecipeContext
from rastro.domain.models import CssRecipe, Representation, Source
from rastro.domain.recipe_bundle import BundleAuthor, canonical_json
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


def parser_for(representation: Representation) -> DocumentParser:
    assert representation == "html"
    return ScraplingParser()


def build(registry: Path) -> None:
    # Keep the original synthetic agenda package and its published verification date.
    publish_to_registry(
        Path(__file__).parent / "recipes/uy-sodre-agenda/1.0.0",
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)
