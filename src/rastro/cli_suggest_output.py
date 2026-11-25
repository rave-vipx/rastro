"""JSON and table output for ``rastro suggest`` and ``rastro new``."""

from __future__ import annotations

import json
from typing import Any

from rastro.application.job_new import GoalJobResult
from rastro.application.job_preview import _cell
from rastro.domain.suggest import Suggestion

MAX_PREVIEW_ROWS = 50


def _print_goal_result(result: GoalJobResult) -> int:
    if result.created is None:
        out: dict[str, Any] = {
            "schema_version": 1,
            "mode": "proposals_only",
            "proposals": [p.model_dump(mode="json") for p in result.proposals],
        }
    else:
        rows = list(result.preview)
        out = {
            "schema_version": 1,
            "created": str(result.created),
            "allowed_hosts": list(result.allowed_hosts),
            "fields": [f.model_dump(mode="json") for f in result.fields],
            "records": len(rows),
            "preview": rows[:MAX_PREVIEW_ROWS],
            "truncated": len(rows) > MAX_PREVIEW_ROWS,
        }
    print(json.dumps(out, indent=2))
    return 0


def suggestion_payload(found: Suggestion[Any]) -> dict[str, Any]:
    rows = [record.data.model_dump(mode="json") for record in found.preview.records]
    return {
        "schema_version": 1,
        "recipe": found.recipe.model_dump(mode="json"),
        "status": str(found.preview.status),
        "records": len(rows),
        "confidence": {k: v.model_dump(mode="json") for k, v in found.confidence.items()},
        "preview": rows[:MAX_PREVIEW_ROWS],
        "truncated": len(rows) > MAX_PREVIEW_ROWS,
    }


def _table(payload: dict[str, Any], names: list[str]) -> str:
    lines = [f"status: {payload['status']} | records: {payload['records']}", " | ".join(names)]
    lines += [" | ".join(_cell(row.get(name)) for name in names) for row in payload["preview"]]
    if payload["truncated"]:
        lines.append(f"(first {MAX_PREVIEW_ROWS} records shown)")
    return "\n".join(lines)
