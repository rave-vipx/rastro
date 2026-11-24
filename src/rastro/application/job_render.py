"""TOML text for a job that ``rastro new`` and the URL-first flow write.

Every value is quoted as a TOML basic string; field names are validated identifiers. A
recipe's page steps are written as ``[[recipe.steps]]`` tables, so a later run replays them
exactly as they were verified.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.job_fields import JobField
from rastro.domain.job_sources import FileJobSource
from rastro.domain.models import CssRecipe
from rastro.domain.page_steps import PageStep
from rastro.domain.recipes import scalar_expression

__all__ = ["render_job", "render_steps"]


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(value: object) -> str:
    return _quote(value) if isinstance(value, str) else str(value)


def render_steps(steps: Sequence[PageStep]) -> list[str]:
    """One ``[[recipe.steps]]`` table per step, ``action`` first, unset options left out."""
    lines: list[str] = []
    for step in steps:
        data = step.model_dump(mode="json", exclude_none=True)
        lines += ["", "[[recipe.steps]]", f"action = {_quote(data.pop('action'))}"]
        lines += [f"{key} = {_value(value)}" for key, value in data.items()]
    return lines


def render_job(job_fields: Sequence[JobField], recipe: CssRecipe, source: Any, **opts: Any) -> str:
    """TOML text for a local or HTTP job; field names are validated identifiers."""
    ds, ident, mem = opts.get("dataset"), opts.get("identity") or (), opts.get("recipe_memory")
    policy: AcquisitionPolicy | None = opts.get("policy")
    sources = (source,) if isinstance(source, FileJobSource) else tuple(source)
    lines = [
        "# Created by `rastro new`. Review the recipe, then preview it with",
        "# `rastro job run <this file>` (prints records, no writes)."
        + (" Store approved records with `rastro job load <this file>`." if ds else ""),
        "format_version = 1",
        *([f"recipe_memory = {_quote(mem)}"] if mem is not None else []),
        "",
    ]
    if policy is not None:
        hosts_toml = ", ".join(_quote(h) for h in policy.allowed_hosts)
        lines += ["[acquisition]", f"allowed_hosts = [{hosts_toml}]"]
        if policy.allow_http:
            lines.append("allow_http = true")
        if recipe.steps or policy.render != "http":  # page steps replay only in a browser
            render = policy.render if policy.render != "http" else "browser"
            lines.append(f"render = {_quote(render)}")
        lines.append("")
    lines += [
        "[recipe]",
        'strategy = "css"',
        f"record_selector = {_quote(recipe.record_selector)}",
        "",
        "[recipe.fields]",
        *(
            f"{name} = {_quote(scalar_expression(selector))}"
            for name, selector in recipe.fields.items()
        ),
        *render_steps(recipe.steps),
        "",
    ]
    if ds is not None:
        stem = re.sub(r"[^a-z0-9_]", "_", Path(ds).stem.lower())
        ds_name = (stem if stem[:1].isalpha() else f"data_{stem}")[:64]
        lines += [
            "[dataset]",
            f"name = {_quote(ds_name)}",
            f"path = {_quote(ds)}",
            f"identity = [{', '.join(_quote(name) for name in ident)}]",
            "",
        ]
    for item in sources:
        if isinstance(item, FileJobSource):
            lines += ["[[sources]]", 'kind = "file"', f"path = {_quote(item.path)}"]
            if item.url is not None:
                lines.append(f"url = {_quote(item.url)}")
        else:
            lines += ["[[sources]]", 'kind = "url"', f"url = {_quote(item.url)}"]
    for field in job_fields:
        lines += ["", "[[fields]]", f"name = {_quote(field.name)}", f"type = {_quote(field.type)}"]
    return "\n".join(lines) + "\n"
