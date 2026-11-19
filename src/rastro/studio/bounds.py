"""N3/N4/N9: domain-validated bounded acquisition settings for Studio."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.domain.recipes import Pagination
from rastro.domain.scroll import ScrollSpec
from rastro.domain.spider import SpiderPolicy
from rastro.studio.editing import edit_policy_table
from rastro.studio.settings import _commit, _format, _read


def numeric_limits(model: type[BaseModel]) -> dict[str, Any]:
    """Expose integer controls directly from the domain's JSON Schema.

    A field that also takes a word (``max_pages = "unbounded"``) keeps its integer bounds.
    """
    limits: dict[str, Any] = {}
    for name, field in model.model_json_schema()["properties"].items():
        number = next(
            (f for f in [field, *field.get("anyOf", [])] if f.get("type") == "integer"), None
        )
        if number is not None:
            limits[name] = {
                "min": number.get("minimum"),
                "max": number.get("maximum"),
                "step": number.get("multipleOf", 1),
                "default": field.get("default"),
            }
    return limits


def controls(job: Job) -> dict[str, Any]:
    modes = [
        getattr(source, "render", "http")
        if "render" in source.model_fields_set
        else (job.acquisition.render if job.acquisition else "http")
        for source in job.sources
    ]
    return {
        "spider": job.spider.model_dump(mode="json", exclude_none=True) if job.spider else {},
        "scroll": job.scroll.model_dump() if job.scroll else None,
        "scroll_disabled": "browser" not in modes,
        "limits": {
            "pagination": numeric_limits(Pagination),
            "spider": numeric_limits(SpiderPolicy),
            "scroll": numeric_limits(ScrollSpec),
        },
    }


def save_bounds(path: Path, expected: str, kind: str, raw: Any) -> str:
    if kind not in {"spider", "scroll"}:
        raise ConfigurationError("Choose spider or scroll settings")
    try:
        policy = (
            None
            if raw is None
            else (
                SpiderPolicy.model_validate(raw)
                if kind == "spider"
                else ScrollSpec.model_validate(raw)
            )
        )
    except ValidationError:
        raise ConfigurationError(
            "Invalid spider or scroll settings: use the displayed ranges; "
            "spider needs one link selector or field"
        ) from None
    text, job = _read(path)
    values = policy.model_dump(mode="json", exclude_none=True) if policy else None
    wanted = job.model_dump(mode="json")
    if values is None:
        wanted.pop(kind, None)
    else:
        assert policy is not None
        wanted[kind] = policy.model_dump(mode="json")
    # Validate cross-field rules before the atomic write, without echoing input.
    try:
        Job.model_validate(wanted)
    except ValidationError:
        raise ConfigurationError(
            "These settings cannot run with this job's sources or fields"
        ) from None
    if _format(path) == "toml":
        newline = "\r\n" if "\r\n" in text else "\n"
        changed = edit_policy_table(text.replace("\r\n", "\n"), kind, values).replace("\n", newline)
    else:
        data = json.loads(text)
        if values is None:
            data.pop(kind, None)
        else:
            data[kind] = values
        changed = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _commit(path, expected, changed, wanted)
