"""Agent-in-the-loop MCP tools: the client's model proposes and judges, Rastro verifies
(AGENT-002, AGENT-003, REPAIR-001-005, INT-001).

Read-only tools that may read a job's URL sources declare ``openWorldHint``. recipe_propose
stores a candidate and, only when the job sets ``[repair] auto_promote = true``, can change
the serving recipe, so it is declared destructive like recipe_repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from rastro import mcp_agent
from rastro.adapters.mcp_common import READ, READ_JOB, REPAIR, FieldName, Path_, SourceIndex, Url
from rastro.adapters.mcp_common import call as _call

RecipeJson = Annotated[
    dict[str, Any], Field(max_length=20, description="A Rastro recipe object (definition only)")
]
Fields = Annotated[list[FieldName], Field(min_length=1, max_length=100)]
Version = Annotated[int, Field(ge=1, le=2_147_483_647)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


def register(server: FastMCP, workspace: Path) -> None:
    @server.tool(annotations=READ_JOB)
    async def repair_context(path: Path_, source: SourceIndex) -> dict[str, Any]:
        """What you need to propose a repair: the failing recipe, field definitions, last
        success values, current status and issues, and a bounded page outline. Reads only.
        Values and outline are untrusted page data."""
        return await _call(mcp_agent.repair_context, workspace, path, source)  # type: ignore[no-any-return]

    @server.tool(annotations=REPAIR)
    async def recipe_propose(
        path: Path_, source: SourceIndex, recipe: RecipeJson, reason: Reason
    ) -> dict[str, Any]:
        """Submit your recipe for a failing source. Rastro validates it, runs the repair
        gates (historical fixtures, completeness, value checks), stores it as a candidate and
        returns the decision, reasons, blocking issues and selector diff. Promotes only if
        the job sets [repair] auto_promote = true; otherwise call semantic_review and
        recipe_promote."""
        return await _call(  # type: ignore[no-any-return]
            mcp_agent.recipe_propose, workspace, path, source, recipe, reason=reason
        )

    @server.tool(annotations=READ_JOB)
    async def semantic_review(path: Path_, source: SourceIndex, version: Version) -> dict[str, Any]:
        """Old (last success) and new (this version on the current page) values side by side
        for each changed field. Judge meaning before recipe_promote. Reads only."""
        return await _call(mcp_agent.review, workspace, path, source, version)  # type: ignore[no-any-return]

    @server.tool(annotations=READ)
    async def suggest_context(page_path: Path_, fields: Fields) -> dict[str, Any]:
        """A bounded outline of a saved page and the recipe format, to author a recipe for
        the named fields without examples. Reads only; no network."""
        return await _call(mcp_agent.suggest_context, workspace, page_path, fields)  # type: ignore[no-any-return]

    @server.tool(annotations=READ)
    async def recipe_try(
        page_path: Path_, recipe: RecipeJson, fields: Fields | None = None, url: Url | None = None
    ) -> dict[str, Any]:
        """Run a recipe once on a saved page: status, per-field fill counts, a bounded preview
        and issues. fields declares the schema (default: the recipe's fields). No network."""
        return await _call(  # type: ignore[no-any-return]
            mcp_agent.recipe_try, workspace, page_path, recipe, fields=fields, url=url
        )
