"""Tell "selector found the value but the schema rejects it" apart from "not reproduced".

A candidate recipe is re-run with every field as optional text; a field is blamed only when
its raw page value reproduces the example yet fails validation with the caller's schema.
Messages describe the page text by length and shape, never by content (INT-002, INT-003).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError, create_model

from rastro.application.engine import Rastro
from rastro.domain.models import CssRecipe, Source
from rastro.domain.suggest import value_matches

HINT = "use a string field, or a BeforeValidator that cleans the text before validation"
_SHAPE_CHARS = 24

type Row = dict[str, Any]


def raw_rows(
    client: Rastro, html: str, source: Source, recipe: CssRecipe, names: Sequence[str]
) -> list[Row]:
    """The candidate's values as text, before any schema type is applied."""
    text_model = create_model(  # type: ignore[call-overload]
        "SuggestText", **{name: (str | None, None) for name in names}
    )
    result = client.extract_html(html, source=source, schema=text_model, recipe=recipe)
    return [record.data.model_dump() for record in result.records]


def failing_fields(model: type[BaseModel], row: Row, source: Source) -> set[str]:
    """Fields of ``row`` whose observed text fails ``model``'s types."""
    observed = {name: value for name, value in row.items() if value is not None}
    context = {"source_url": source.evidence_url, "timezone": source.timezone}
    try:
        model.model_validate(observed, context={**context, "observed_at": datetime.now(UTC)})
    except ValidationError as error:
        locations = (item["loc"][0] for item in error.errors() if item["loc"])
        return {str(name) for name in locations if observed.get(str(name)) is not None}
    return set()


def describe(model: type[BaseModel], name: str, text: str) -> str:
    """``field 'price' (Decimal): page text of 9 characters shaped '99.99 AAA'``."""
    annotation = model.model_fields[name].annotation
    kind = getattr(annotation, "__name__", None) or str(annotation)
    return (
        f"field {name!r} ({kind[:40]}): page text of {len(text)} characters shaped {_shape(text)!r}"
    )


def _shape(text: str) -> str:
    # Only digits, letters, ASCII punctuation and spaces survive: no page control characters.
    shape = "".join(
        "9"
        if c.isdigit()
        else "A"
        if c.isalpha()
        else c
        if c.isascii() and c.isprintable()
        else "?"
        for c in text
    )
    return shape if len(shape) <= _SHAPE_CHARS else shape[: _SHAPE_CHARS - 1] + "…"


def example_type_failures(
    model: type[BaseModel],
    rows: Sequence[Row],
    examples: Sequence[dict[str, str]],
    source: Source,
) -> list[str]:
    """Descriptions of example fields found by the selectors but rejected by the schema.

    Empty when some example is not reproduced even as text: that is a selector problem.
    """
    blamed: dict[str, str] = {}
    for example in examples:
        row = next(
            (
                row
                for row in rows
                if all(value_matches(v, row.get(k), source.url) for k, v in example.items())
            ),
            None,
        )
        if row is None:
            return []
        for name in sorted(failing_fields(model, row, source)):
            blamed.setdefault(name, describe(model, name, str(row[name])))
    return list(blamed.values())


def record_type_failures(
    model: type[BaseModel], rows: Sequence[Row], source: Source
) -> dict[str, str]:
    """Per field, a note for page values that fail the schema type in some records."""
    counts: dict[str, int] = {}
    first: dict[str, str] = {}
    for row in rows:
        for name in failing_fields(model, row, source):
            counts[name] = counts.get(name, 0) + 1
            first.setdefault(name, describe(model, name, str(row[name])))
    return {
        name: f"{count} page value(s) fail the schema type, e.g. {first[name]}; {HINT}"
        for name, count in counts.items()
    }
