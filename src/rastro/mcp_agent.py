"""MCP agent-in-the-loop tools: the client's own model proposes, Rastro verifies (AGENT-002,
AGENT-003, REPAIR-001-005, INT-001, QUAL-006).

The agent never supplies records. It reads bounded context, proposes recipe definitions and
judges meaning; every proposal passes the same validation and recipe CI as deterministic
repair. Paths are workspace-relative and outputs bounded. Nothing here calls a model, so
any MCP client works the same way: no provider is special-cased.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rastro.application.agent_repair import propose_job_recipe
from rastro.application.agent_review import RECIPE_FORMAT, job_repair_context, semantic_review
from rastro.application.job_loader import load_job
from rastro.application.page_digest import digest_page
from rastro.application.recipe_diff import with_diffs
from rastro.domain.lifecycle_history import check_reason
from rastro.domain.models import ConfigurationError, Limits, Recipe
from rastro.mcp_authoring import _schema
from rastro.mcp_tools import _existing, _plain, bounded
from rastro.scrape import scrape
from rastro.scrape_target import read_bounded

MAX_RECIPE_BYTES = 64 * 1024
PREVIEW_ROWS = 20


def parse_recipe(raw: Mapping[str, Any]) -> Recipe:
    """A strict Rastro recipe from agent JSON; refuses oversized or unknown shapes."""
    try:
        text = json.dumps(dict(raw))
    except (TypeError, ValueError):
        raise ConfigurationError("recipe is not a valid Rastro recipe") from None
    if len(text.encode()) > MAX_RECIPE_BYTES:
        raise ConfigurationError("recipe exceeds 64 KiB")
    try:
        return Recipe.model_validate_json(text, strict=True)
    except (ValidationError, ValueError):
        raise ConfigurationError("recipe is not a valid Rastro recipe") from None


def _with_outline(payload: dict[str, Any], outline: str | None) -> dict[str, Any]:
    """Bound every value, then attach the outline, which carries its own size limit."""
    result = bounded(payload, max_chars=300)
    result["page_outline"] = outline
    return result


def repair_context(root: Path, path: str, source: int) -> dict[str, Any]:
    """Failing recipe, fields, last success values, current issues and a page outline."""
    payload = job_repair_context(_existing(root, path), source, allow_absolute_paths=False)
    outline = payload.pop("page_outline")
    return _with_outline(payload, outline)


def recipe_propose(
    root: Path, path: str, source: int, recipe: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    """Gate an agent's recipe like a repair candidate; store it; return the evaluation."""
    try:
        reason = check_reason(reason)
    except ValidationError:
        raise ConfigurationError("reason must be 1 to 500 printable characters") from None
    proposal = parse_recipe(recipe)
    job_path = _existing(root, path)
    report = propose_job_recipe(job_path, source, proposal, allow_absolute_paths=False)
    payload = with_diffs(load_job(job_path), job_path.parent, source, _plain(report))
    (comparison,) = payload["sources"][0]["comparisons"]
    evaluation = comparison.get("evaluation") or {}
    payload["decision"] = evaluation.get("decision")
    payload["candidate_version"] = comparison["candidate_version"]
    payload["agent_reason"] = reason
    payload["next"] = _next_step(payload["decision"], payload["sources"][0]["promoted_version"])
    return bounded(payload)


def _next_step(decision: str | None, promoted: int | None) -> str:
    if promoted is not None:
        return "Promoted by the job's auto_promote; run recipe_apply to write it to the job file"
    if decision == "reject":
        return "Rejected: read the reasons and blocking issues, then propose a corrected recipe"
    return "Quarantined: call semantic_review, then recipe_promote with a reason if it is right"


def review(root: Path, path: str, source: int, version: int) -> dict[str, Any]:
    """Old and new values side by side for each changed field of a stored version."""
    return bounded(semantic_review(_existing(root, path), source, version), max_chars=300)


def suggest_context(root: Path, page_path: str, fields: Sequence[str]) -> dict[str, Any]:
    """A page outline and the recipe format, to author a recipe for a new saved page."""
    _schema(fields)  # validates names and count
    html = read_bounded(_existing(root, page_path), Limits().max_html_bytes)
    payload = {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "fields": list(dict.fromkeys(fields)),
        "recipe_format": RECIPE_FORMAT,
        "next": "Call recipe_try with a recipe; iterate until every row is right",
    }
    return _with_outline(payload, digest_page(html, limits=Limits()))


def recipe_try(
    root: Path,
    page_path: str,
    recipe: Mapping[str, Any],
    *,
    fields: Sequence[str] | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Run a recipe once on a saved page: a bounded preview, per-field fill counts, issues."""
    parsed = parse_recipe(recipe)
    names = list(fields) if fields else list(parsed.fields)
    result = scrape(
        _existing(root, page_path),
        _schema(names),
        recipe=parsed,
        url=url,
        repair="off",
    )
    rows = [record.data.model_dump(mode="json") for record in result.records]
    filled = {name: sum(row.get(name) not in (None, "") for row in rows) for name in names}
    payload = {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "status": str(result.status),
        "total": len(rows),
        "filled": filled,
        "preview": rows[:PREVIEW_ROWS],
        "issues": [issue.model_dump(mode="json") for issue in result.issues[:20]],
        "truncated": len(rows) > PREVIEW_ROWS,
    }
    return bounded(payload, max_chars=300)
