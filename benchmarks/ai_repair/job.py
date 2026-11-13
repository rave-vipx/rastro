"""The schema and job file every case runs under (QUAL-001, REPAIR-001).

``SCHEMA`` documents each field's meaning. The job file cannot carry descriptions, and
this base's repair prompt sends field names only, so the descriptions are harness
metadata for people and for future semantic checks, not model input.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA: tuple[dict[str, Any], ...] = (
    {"name": "id", "type": "string", "description": "Stable event identifier"},
    {"name": "title", "type": "string", "description": "Name of the show, not the artist"},
    {
        "name": "performer",
        "type": "string",
        "description": "Artist, band or company performing; never the show name",
    },
    {"name": "starts_at", "type": "datetime", "description": "Start time with UTC offset"},
    {"name": "url", "type": "url", "description": "Ticket or detail page link"},
    {
        "name": "price",
        "type": "decimal",
        "required": False,
        "description": "Ticket price in pesos; some events omit it",
    },
)


def _string(value: str) -> str:
    return json.dumps(value)  # a TOML basic string for these ASCII values


def job_toml(recipe: dict[str, Any], model: dict[str, Any] | None = None) -> str:
    """A file-source job with recipe memory, a dataset and automatic promotion."""
    lines = [
        "format_version = 1",
        'recipe_memory = "recipes.sqlite"',
        "",
        "[repair]",
        "auto_promote = true",
        "min_records = 5",
        "",
        "[dataset]",
        'name = "ai_repair"',
        'path = "data.sqlite"',
        'identity = ["id"]',
        "",
    ]
    if model is not None:
        lines += ["[model]", *(f"{k} = {_string(str(v))}" for k, v in model.items()), ""]
        # Default call budget (2 calls, 1 retry after invalid output); local CPUs are slow.
        lines += ["[inference_policy]", "timeout_seconds = 180", ""]
    lines += [
        "[[sources]]",
        'kind = "file"',
        'path = "page.html"',
        'url = "https://agenda.example/eventos/"',
        "",
        "[sources.recipe]",
        "version = 1",
        'strategy = "css"',
        f"record_selector = {_string(recipe['record_selector'])}",
        "require_evidence = true",
        "",
        "[sources.recipe.fields]",
        *(f"{k} = {_string(v)}" for k, v in recipe["fields"].items()),
        "",
    ]
    for field in SCHEMA:
        lines += ["[[fields]]", f"name = {_string(field['name'])}"]
        lines.append(f"type = {_string(field['type'])}")
        if field.get("required") is False:
            lines.append("required = false")
        lines.append("")
    return "\n".join(lines)
