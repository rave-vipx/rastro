"""MCP authoring tools: suggest a recipe, create a job, extract once from a saved page
(AGENT-002, INT-001, INT-002, JOB-001).

Pages are workspace files read with a byte bound and never through a symlink. These tools
never fetch the network: ``url`` only sets the address used for relative links.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from rastro.application.job_new import new_job
from rastro.application.suggest import suggest
from rastro.cli_suggest import suggestion_payload
from rastro.domain.job_fields import StringField, build_job_schema
from rastro.domain.models import ConfigurationError, Limits, Recipe
from rastro.domain.selector_policy import (
    check_untrusted_field,
    check_untrusted_recipe,
    check_untrusted_selector,
)
from rastro.domain.suggest import check_examples
from rastro.mcp_tools import _existing, _inside, bounded
from rastro.scrape import scrape
from rastro.scrape_target import read_bounded

MAX_FIELDS = 100


def _schema(names: Sequence[str]) -> type[BaseModel]:
    if not 1 <= len(names) <= MAX_FIELDS:
        raise ConfigurationError(f"Give between 1 and {MAX_FIELDS} fields")
    try:
        return build_job_schema([StringField(name=name) for name in dict.fromkeys(names)])
    except ValidationError:
        raise ConfigurationError(
            "Field names must start with a letter and use letters, digits or _"
        ) from None


def suggest_recipe(
    root: Path, page_path: str, examples: Sequence[Mapping[str, str]], *, url: str | None = None
) -> dict[str, Any]:
    """A verified CSS recipe for a saved page plus its preview rows, as `rastro suggest`."""
    html = read_bounded(_existing(root, page_path), Limits().max_html_bytes)
    checked = check_examples(examples)
    schema = _schema([name for example in checked for name in example])
    found = suggest(html, schema, examples=checked, url=url)
    return bounded({"data_trust": "untrusted_source_data", **suggestion_payload(found)})


def job_new(
    root: Path,
    path: str,
    page_path: str,
    examples: Sequence[Mapping[str, str]],
    *,
    url: str | None = None,
    dataset: str | None = None,
    identity: str | Sequence[str] = (),
    recipe_memory: str | None = None,
) -> dict[str, Any]:
    """Write a new job file from a saved page and examples; refuses to overwrite."""
    new_job(
        _inside(root, path),
        page=_existing(root, page_path),
        examples=examples,
        url=url,
        dataset=dataset,
        identity=(identity,) if isinstance(identity, str) else tuple(identity),
        recipe_memory=recipe_memory,
    )
    return {"schema_version": 1, "created": path}


def scrape_file(
    root: Path,
    page_path: str,
    *,
    fields: Mapping[str, str] | None = None,
    records: str | None = None,
    recipe: Mapping[str, Any] | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Extract records once from a saved page with CSS ``fields`` or a full ``recipe``."""
    if (fields is None) == (recipe is None):
        raise ConfigurationError("Give either fields (with optional records) or recipe")
    parsed: Recipe | None = None
    if recipe is not None:
        if records is not None:
            raise ConfigurationError("recipe replaces records; omit records")
        try:
            parsed = Recipe.model_validate(dict(recipe))
        except (ValidationError, ValueError):
            raise ConfigurationError("recipe is not a valid Rastro recipe") from None
        check_untrusted_recipe(parsed)
    for expression in (fields or {}).values():
        check_untrusted_field(expression)
    if records is not None:
        check_untrusted_selector(records)
    names = list(fields or parsed.fields)  # type: ignore[union-attr]
    result = scrape(
        _existing(root, page_path),
        _schema(names),
        records=records,
        fields=fields,
        recipe=parsed,
        url=url,
        repair="off",
    )
    payload = {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "status": str(result.status),
        "total": len(result.records),
        "records": [record.data.model_dump(mode="json") for record in result.records],
        "issues": [issue.model_dump(mode="json") for issue in result.issues],
    }
    return bounded(payload)
