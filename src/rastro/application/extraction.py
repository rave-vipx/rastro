"""One deterministic extraction attempt with schema validation and provenance."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from rastro.domain.models import (
    ConfigurationError,
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Issue,
    Limits,
    Metrics,
    Outcome,
    Recipe,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)
from rastro.ports.parser import DocumentParser


def extract_document[T: BaseModel](
    html: str,
    *,
    source: Source,
    schema: type[T],
    recipe: Recipe,
    parser: DocumentParser,
    limits: Limits,
) -> ExtractionResult[T]:
    """Validate each candidate, preserving valid records and reporting every rejection.

    Recipe keys use model field names. Pydantic's coercion and custom validators
    follow the supplied model configuration. Source values are never logged.
    """
    started = perf_counter()
    started_at = datetime.now(UTC)
    job_id = str(uuid4())
    size = len(html.encode("utf-8"))
    if size > limits.max_html_bytes:
        raise ResourceLimitError("HTML input exceeds max_html_bytes")
    extraction_start = perf_counter()
    parsed = parser.parse(html, recipe, limits)
    if len(parsed.candidates) > limits.max_records:
        raise ResourceLimitError("Parser returned more than max_records")
    extraction_seconds = perf_counter() - extraction_start
    validation_start = perf_counter()
    records: list[ExtractionRecord[T]] = []
    issues = list(parsed.issues)
    rejected = 0
    required = {name for name, field in schema.model_fields.items() if field.is_required()}
    for index, candidate in enumerate(parsed.candidates):
        if candidate.issues:
            issues.extend(candidate.issues)
            rejected += 1
            continue
        if not candidate.values:
            issues.append(Issue(code="missing_fields", record_index=index))
            rejected += 1
            continue
        if any(name not in candidate.locators for name in candidate.values) or (
            recipe.require_evidence and not required.issubset(candidate.values)
        ):
            issues.append(Issue(code="missing_evidence", record_index=index))
            rejected += 1
            continue
        try:
            data = schema.model_validate(
                dict(candidate.values),
                context={
                    "locale": source.locale,
                    "languages": source.languages,
                    "timezone": source.timezone,
                    "currency": source.currency,
                    "egress": source.egress,
                },
            )
        except ValidationError as error:
            for detail in error.errors(
                include_url=False, include_context=False, include_input=False
            ):
                # Validator messages can contain secrets; only expose a stable category.
                field = str(detail["loc"][0]) if detail["loc"] else None
                issues.append(Issue(code="validation_error", record_index=index, field=field))
            rejected += 1
            continue
        evidence = {
            name: Evidence(
                source_url=source.evidence_url,
                observed_at=started_at,
                method=recipe.strategy,
                locator=locator,
                recipe_version=recipe.version,
            )
            for name, locator in candidate.locators.items()
            if name in candidate.values
        }
        records.append(ExtractionRecord(data=data, evidence=evidence))
    validation_seconds = perf_counter() - validation_start
    if records:
        status = Outcome.PARTIAL_SUCCESS if issues else Outcome.SUCCESS
    elif any(issue.code in {"ambiguous_match", "missing_evidence"} for issue in issues):
        status = Outcome.INSUFFICIENT_EVIDENCE
    elif any(issue.code in {"validation_error", "invalid_jsonld"} for issue in issues):
        status = Outcome.INVALID_DATA
    else:
        status = Outcome.MISSING_DATA
    result = ExtractionResult[T](
        job_id=job_id,
        status=status,
        source_url=source.evidence_url,
        locale=source.locale,
        timezone=source.timezone,
        schema_fingerprint=schema_fingerprint(schema),
        started_at=started_at,
        completed_at=datetime.now(UTC),
        records=tuple(records),
        issues=tuple(issues),
        metrics=Metrics(
            input_bytes=size,
            candidates=len(parsed.candidates),
            accepted=len(records),
            fields_validated=sum(len(record.evidence) for record in records),
            rejected=rejected,
            extraction_seconds=extraction_seconds,
            validation_seconds=validation_seconds,
            total_seconds=perf_counter() - started,
        ),
    )
    return result


def validate_recipe(schema: type[BaseModel], recipe: Recipe) -> Recipe:
    # Revalidate mutable mappings at the trust boundary, even on a frozen model.
    recipe = Recipe.model_validate(recipe.model_dump())
    fields = schema.model_fields
    if not fields or any(
        name not in fields for item in (recipe, *recipe.fallbacks) for name in item.fields
    ):
        raise ConfigurationError("Recipe mappings must reference declared schema fields")
    if any(field.alias or field.validation_alias for field in fields.values()):
        raise ConfigurationError("Schema aliases are not supported in this milestone")
    return recipe
