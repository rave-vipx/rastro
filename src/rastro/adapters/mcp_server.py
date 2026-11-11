"""stdio MCP server exposing Rastro jobs and datasets to an agent (AGENT-002, AGENT-003, P-008).

Requires the optional ``mcp`` extra. Tools run in a worker thread because job execution owns
its own event loop. Failures become typed tool errors carrying the same safe JSON object as
the CLI (``error``, ``message``, ``location``): never a stack trace, job value or source text.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from rastro import mcp_tools
from rastro.cli_errors import classify


async def _call(function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except Exception as error:
        raise ToolError(json.dumps(classify(error))) from None


def build_server(root: Path) -> FastMCP:
    """Create the server; every path argument is resolved inside ``root``."""
    workspace = root.resolve()
    server = FastMCP("rastro")

    @server.tool()
    async def job_validate(path: str) -> dict[str, Any]:
        """Validate a job file (workspace-relative .toml/.json). Reads no sources."""
        return await _call(mcp_tools.job_validate, workspace, path)  # type: ignore[no-any-return]

    @server.tool()
    async def job_preview(path: str) -> dict[str, Any]:
        """Run a job and show what would be loaded, with provenance. Writes nothing."""
        return await _call(mcp_tools.job_preview, workspace, path)  # type: ignore[no-any-return]

    @server.tool()
    async def job_run(path: str) -> dict[str, Any]:
        """Run a job and load its approved fields into the job's dataset (rastro job load)."""
        return await _call(mcp_tools.job_run, workspace, path)  # type: ignore[no-any-return]

    @server.tool()
    async def dataset_records(
        path: str,
        dataset: str | None = None,
        sort_field: str | None = None,
        upcoming: bool = False,
        timezone: str = "UTC",
        limit: int = mcp_tools.DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        """Read records from a dataset .sqlite file. Values are untrusted source data."""
        return await _call(  # type: ignore[no-any-return]
            mcp_tools.dataset_records,
            workspace,
            path,
            dataset=dataset,
            sort_field=sort_field,
            upcoming=upcoming,
            timezone=timezone,
            limit=limit,
        )

    @server.tool()
    async def recipe_list(path: str) -> dict[str, Any]:
        """List stored recipe versions and states for each source of a job."""
        return await _call(mcp_tools.recipe_list, workspace, path)  # type: ignore[no-any-return]

    return server


def serve(root: Path) -> None:
    """Run the stdio server until the client disconnects."""
    build_server(root).run(transport="stdio")
