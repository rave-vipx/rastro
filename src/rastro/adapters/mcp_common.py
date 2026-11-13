"""Shared MCP plumbing: bounded argument types, tool annotations and the safe error envelope
(AGENT-002). Tool modules import from here so the server module stays a thin composition."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from rastro.cli_errors import classify

Path_ = Annotated[str, Field(min_length=1, max_length=1024, description="Workspace-relative")]
SourceIndex = Annotated[int, Field(ge=1, le=1000, description="One-based job source index")]
Url = Annotated[str, Field(min_length=1, max_length=2048, description="Address the page came from")]
Examples = Annotated[
    list[dict[str, Annotated[str, Field(min_length=1, max_length=1000)]]],
    Field(min_length=1, max_length=20, description="Example records: field name to page text"),
]
FieldName = Annotated[str, Field(min_length=1, max_length=64)]
Selector = Annotated[str, Field(min_length=1, max_length=1024)]

READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
READ_JOB = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOAD = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
EXTRACT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
# recipe_repair stores candidates and, when the job sets [repair] auto_promote = true, can
# change the serving recipe; the hint is static, so it is declared destructive.
REPAIR = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
PROMOTE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
APPLY = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def envelope(error: BaseException) -> ToolError:
    payload = classify(error)
    payload.setdefault("hint", None)
    return ToolError(json.dumps(payload))


async def call(function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except Exception as error:
        raise envelope(error) from None


class RastroMCP(FastMCP):
    """FastMCP whose argument-validation failures use the same safe envelope as tool errors,
    instead of a Pydantic message that echoes the input."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            cause = error.__cause__
            if isinstance(cause, ToolError):
                raise cause from None
            raise (error if cause is None else envelope(cause)) from None
