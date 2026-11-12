"""Machine-readable error envelopes shared by the CLI, Studio and MCP (AGENT-001).

Every error is ``{"error": <code>, "message": <safe text>, "location": <key or null>}``
with an optional ``"hint"``.
"""

import re

_EXTRA = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def missing_extra_error(extra: str) -> dict[str, str | None]:
    """The envelope for a feature whose optional dependency extra is not installed."""
    if not _EXTRA.fullmatch(extra):
        raise ValueError("Extra names are lowercase letters, digits and hyphens")
    return {
        "error": "missing_extra",
        "message": f"This command needs the optional '{extra}' extra",
        "location": None,
        "hint": f"pip install 'rastro-engine[{extra}]'",
    }
