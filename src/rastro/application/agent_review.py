"""Read-only evidence for an agent that proposes or judges a repair (AGENT-002, REPAIR-001).

``job_repair_context`` gives a model what it needs to propose a fix: the failing recipe, the
field definitions, recent success values, the current outcome and a page outline.
``semantic_review`` puts the values a stored candidate extracts now next to the last
successful values, field by field, so the agent can judge meaning before promotion.
Neither creates or writes a store. All values are untrusted source data.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.extraction import extract_document
from rastro.application.job_repair import Evidence, gather_evidence, job_policy, run_sync
from rastro.application.page_outline import outline_page
from rastro.application.repair_context import RepairContext, safe_path
from rastro.domain.models import ConfigurationError, Limits, RastroError, Recipe
from rastro.domain.recipe_compare import RecipeComparison

TRUST = "untrusted_source_data"
MAX_SAMPLE = 10
RECIPE_FORMAT = (
    'CSS recipe: {"strategy": "css", "record_selector": "<one element per record>", '
    '"fields": {"<field>": "<selector relative to the record>::text or ::attr(name)"}}. '
    "Map only declared fields; include every required field."
)


def _read_only(path: Path, allow: bool) -> RepairContext:
    context = RepairContext.load(path, allow)
    assert context.job.recipe_memory is not None  # RepairContext.load requires it
    memory = safe_path(context.job.recipe_memory, context.base, allow)
    if not memory.is_file():
        raise ConfigurationError("Load a successful recipe before repairing this source")
    return dataclasses.replace(context, repository=SQLiteRecipeRepository(memory, read_only=True))


def _gather(context: RepairContext, source: int) -> Evidence:
    return run_sync(gather_evidence(context, source, job_policy(context, {})))


def _extract(context: RepairContext, evidence: Evidence, recipe: Recipe) -> dict[str, Any]:
    """Status, values and issue codes ``recipe`` yields on the current page."""
    assert evidence.parser is not None
    try:
        result = extract_document(
            evidence.document,
            source=evidence.source,
            schema=context.schema,
            recipe=recipe.model_copy(update={"fallbacks": ()}),
            parser=evidence.parser,
            limits=context.job.limits or Limits(),
        )
    except RastroError as error:
        return {"status": "failed", "error": type(error).__name__, "rows": [], "issues": []}
    return {
        "status": str(result.status),
        "rows": [r.data.model_dump(mode="json") for r in result.records],
        "issues": [i.model_dump(mode="json") for i in result.issues[:20]],
    }


def _last_success(evidence: Evidence) -> list[dict[str, Any]]:
    if not evidence.fixtures:
        return []
    return [dict(record.data) for record in evidence.fixtures[0].expected.records]


def job_repair_context(
    path: str | Path, source: int, *, allow_absolute_paths: bool = False
) -> dict[str, Any]:
    """What a model needs to propose a repair for one source; reads only."""
    context = _read_only(Path(path), allow_absolute_paths)
    evidence = _gather(context, source)
    current = _extract(context, evidence, evidence.previous)
    html = evidence.previous.representation == "html"
    return {
        "schema_version": 1,
        "data_trust": TRUST,
        "source": source,
        "source_url": evidence.source.url,
        "representation": evidence.previous.representation,
        "recipe": evidence.previous.model_dump(mode="json"),
        "fields": [field.model_dump(mode="json") for field in context.job.fields],
        "current": {
            "status": current["status"],
            "records": len(current["rows"]),
            "issues": current["issues"],
        },
        "last_success_sample": _last_success(evidence)[:MAX_SAMPLE],
        "page_outline": outline_page(evidence.document) if html else None,
        "recipe_format": RECIPE_FORMAT if html else None,
    }


def _column(rows: Sequence[dict[str, Any]], name: str) -> list[Any]:
    return [row.get(name) for row in rows[:MAX_SAMPLE]]


def semantic_review(
    path: str | Path, source: int, version: int, *, allow_absolute_paths: bool = False
) -> dict[str, Any]:
    """Old (last success) and new (candidate on the current page) values per changed field."""
    context = _read_only(Path(path), allow_absolute_paths)
    evidence = _gather(context, source)
    stored = context.repository.get(evidence.key, version=version)
    if stored is None:
        raise ConfigurationError("No stored recipe version with that number for this source")
    comparison = RecipeComparison.between(evidence.previous, stored.recipe)
    # Candidates always require evidence; only locator settings change which values match.
    settings = [name for name in comparison.changed_settings if name != "require_evidence"]
    changed = tuple(context.schema.model_fields) if settings else comparison.changed_fields
    old = _last_success(evidence)
    new = _extract(context, evidence, stored.recipe)
    return {
        "schema_version": 1,
        "data_trust": TRUST,
        "source": source,
        "version": version,
        "state": str(stored.metadata.state),
        "serving_version": evidence.previous.version,
        "changed_fields": list(comparison.changed_fields),
        "changed_settings": settings,
        "old_from": "last_success_sample" if old else None,
        "new_status": new["status"],
        "old_records": len(old),
        "new_records": len(new["rows"]),
        "fields": [
            {"field": name, "old": _column(old, name), "new": _column(new["rows"], name)}
            for name in changed
        ],
        "question": (
            "Does each new value mean the same thing as the old value of that field "
            "(same kind of data, not a neighbouring field)? Promote only if yes."
        ),
    }
