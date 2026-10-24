"""Small offline CLI. Stdout is JSON; exit codes distinguish failed operations."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.metadata import version
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, Field, ValidationError

from rastro import Outcome, Rastro, RastroError, Recipe, Source, __version__


class _DemoProduct(BaseModel):
    name: str = Field(min_length=1)
    price: float = Field(ge=0, allow_inf_nan=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rastro: verifiable, schema-first extraction")
    parser.add_argument("--version", action="version", version=f"rastro {__version__}")
    parser.add_argument("--verbose", action="store_true", help="Write structured logs to stderr")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="Run a synthetic JSON-LD extraction without network access")
    subcommands.add_parser("doctor", help="Report installed core versions and capabilities as JSON")
    validate = subcommands.add_parser("validate-recipe", help="Validate a local declarative recipe")
    validate.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    handler: logging.StreamHandler[TextIO] | None = None
    logger = logging.getLogger("rastro")
    previous_level = logger.level
    if args.verbose:
        handler = logging.StreamHandler(sys.stderr)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    try:
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "rastro": __version__,
                        "python": sys.version.split()[0],
                        "dependencies": {
                            name: version(name) for name in ("pydantic", "scrapling", "structlog")
                        },
                        "capabilities": [
                            "offline_html",
                            "jsonld",
                            "css",
                            "pydantic_validation",
                            "provenance",
                        ],
                    }
                )
            )
            return 0
        if args.command == "validate-recipe":
            with args.path.open("rb") as file:
                raw = file.read(65_537)
            if len(raw) > 65_536:
                raise RastroError("Recipe exceeds 64 KiB")
            recipe = Recipe.model_validate_json(raw)
            print(recipe.model_dump_json())
            return 0
        result = Rastro().extract_html(
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Trail notebook","offers":{"price":12.5}}'
            "</script>",
            source=Source(url="https://example.com/products", locale="en-US"),
            schema=_DemoProduct,
            recipe=Recipe(
                strategy="jsonld",
                item_type="Product",
                fields={"name": "/name", "price": "/offers/price"},
            ),
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.status == Outcome.SUCCESS else 1
    except (OSError, ValueError, ValidationError, RastroError):
        print(
            json.dumps(
                {"error": "invalid_input", "hint": "Check input, recipe, and resource limits"}
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(previous_level)


if __name__ == "__main__":
    raise SystemExit(main())
