"""Suggest a verified CSS recipe from a saved page and example values (INT-002, DISC-003).

Candidates come from a ``RecipeSuggester`` port; each is run through the normal extraction
pipeline with the caller's schema, and only a recipe that reproduces every example is
returned. Deterministic and offline: no model, no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, overload

from pydantic import BaseModel, TypeAdapter, ValidationError

from rastro.application.engine import Rastro
from rastro.application.suggest_diagnosis import (
    HINT,
    example_type_failures,
    raw_rows,
    record_type_failures,
)
from rastro.domain.job_fields import JobField, build_job_schema
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.domain.suggest import (
    Example,
    FieldConfidence,
    Suggestion,
    SuggestionError,
    check_examples,
    value_matches,
)
from rastro.ports.suggest import RecipeCandidate, RecipeSuggester

LOCAL_URL = "https://local.invalid/suggest"
MAX_CANDIDATES = 8
type SchemaInput = type[BaseModel] | Sequence[JobField | Mapping[str, Any]]
_FIELDS = TypeAdapter(tuple[JobField, ...])


def schema_model(schema: SchemaInput) -> type[BaseModel]:
    """Accept a Pydantic model or job field definitions (objects or mappings)."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    if isinstance(schema, str | bytes) or not isinstance(schema, Sequence):
        raise ConfigurationError("schema must be a Pydantic model or a list of job fields")
    try:
        fields = _FIELDS.validate_python(tuple(schema))
    except ValidationError:
        raise ConfigurationError("schema contains an invalid job field definition") from None
    return build_job_schema(fields)


@overload
def suggest[T: BaseModel](
    html: str,
    schema: type[T],
    *,
    examples: Sequence[Example],
    url: str | None = None,
    records_min: int = 2,
    limits: Limits | None = None,
    _suggester: RecipeSuggester | None = None,
) -> Suggestion[T]: ...
@overload
def suggest(
    html: str,
    schema: Sequence[JobField | Mapping[str, Any]],
    *,
    examples: Sequence[Example],
    url: str | None = None,
    records_min: int = 2,
    limits: Limits | None = None,
    _suggester: RecipeSuggester | None = None,
) -> Suggestion[BaseModel]: ...
def suggest(
    html: str,
    schema: SchemaInput,
    *,
    examples: Sequence[Example],
    url: str | None = None,
    records_min: int = 2,
    limits: Limits | None = None,
    _suggester: RecipeSuggester | None = None,
) -> Suggestion[Any]:
    """Build, verify and return a CSS recipe that reproduces every example on ``html``."""
    model = schema_model(schema)
    limits = limits or Limits()
    if not 1 <= records_min <= limits.max_records:
        raise ConfigurationError("records_min must be between 1 and limits.max_records")
    if len(html.encode()) > limits.max_html_bytes:
        raise ResourceLimitError("HTML input exceeds max_html_bytes")
    checked = check_examples(examples)
    names = tuple(model.model_fields)
    unknown = sorted({key for example in checked for key in example} - set(names))
    if unknown:
        raise ConfigurationError(f"Example fields are not in the schema: {', '.join(unknown)}")
    source = Source(url=url or LOCAL_URL)
    # ``_suggester`` is a private test seam, not part of the public signature.
    suggester = _suggester
    if suggester is None:
        from rastro.adapters.scrapling_suggest import ScraplingSuggester

        suggester = ScraplingSuggester()
    candidates = suggester.suggest(
        html,
        names,
        checked,
        limits,
        records_min=records_min,
        max_candidates=MAX_CANDIDATES,
        base_url=url,
    )
    return _verify(html, model, source, checked, candidates, limits)


def _verify[T: BaseModel](
    html: str,
    model: type[T],
    source: Source,
    examples: Sequence[dict[str, str]],
    candidates: Sequence[RecipeCandidate],
    limits: Limits,
) -> Suggestion[T]:
    required = {name for name, info in model.model_fields.items() if info.is_required()}
    client = Rastro(limits=limits)
    fallback: Suggestion[T] | None = None
    missing: list[str] = []
    type_errors: list[str] = []
    names = tuple(model.model_fields)
    for candidate in candidates:
        unmapped = sorted(required - set(candidate.recipe.fields))
        if unmapped:
            missing = [f"required field {name!r} has no example" for name in unmapped]
            continue
        result = client.extract_html(html, source=source, schema=model, recipe=candidate.recipe)
        missing = _unreproduced(examples, result, source.url)
        if missing:
            rows = raw_rows(client, html, source, candidate.recipe, names)
            type_errors = type_errors or example_type_failures(model, rows, examples, source)
            continue
        notes = _confidence(candidate.confidence, examples, result)
        if result.status is not Outcome.SUCCESS:
            rows = raw_rows(client, html, source, candidate.recipe, names)
            for name, note in record_type_failures(model, rows, source).items():
                notes[name] = notes[name].model_copy(update={"note": note})
        found = Suggestion[T](recipe=candidate.recipe, preview=result, confidence=notes)
        if result.status is Outcome.SUCCESS:
            return found
        if fallback is None and result.status is Outcome.PARTIAL_SUCCESS:
            fallback = found
    if fallback is not None:
        return fallback
    if type_errors:
        raise SuggestionError(
            "The examples were found and the selectors work, but the page values fail the "
            f"schema type: {'; '.join(type_errors)}. Fix: {HINT}"
        )
    raise SuggestionError("No candidate recipe reproduced the examples: " + ", ".join(missing))


def _unreproduced(
    examples: Sequence[dict[str, str]], result: ExtractionResult[Any], base_url: str
) -> list[str]:
    rows = [record.data.model_dump() for record in result.records]
    missing: list[str] = []
    for position, example in enumerate(examples, 1):
        if not any(
            all(value_matches(value, row.get(name), base_url) for name, value in example.items())
            for row in rows
        ):
            missing.extend(f"example {position} {name!r}" for name in example)
    return missing


def _confidence(
    notes: Mapping[str, FieldConfidence],
    examples: Sequence[dict[str, str]],
    result: ExtractionResult[Any],
) -> dict[str, FieldConfidence]:
    rows = [record.data.model_dump() for record in result.records]
    return {
        name: note.model_copy(
            update={
                "examples_matched": sum(name in example for example in examples)
                if note.method == "example"
                else 0,
                "records_with_value": sum(row.get(name) is not None for row in rows),
            }
        )
        for name, note in notes.items()
    }
