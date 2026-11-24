"""Change one operator-owned job property, reparse, compare, then replace atomically.

Two edits exist: a recipe's existing ``pagination`` (ADR 0026, no new engine) and the
job's ``[repair] auto_promote``. TOML files keep their comments and layout outside the
edited table; JSON files are rewritten with the same data, like ``recipe_apply``. The new
file must parse to the old job with only that property changed, or nothing is written.
Neither edit can write a stealth or network grant: those stay operator flags.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from rastro.application.job_loader import validate_job_paths
from rastro.application.recipe_toml import recipe_table, replace_recipe
from rastro.domain.jobs import Job, parse_job
from rastro.domain.models import ConfigurationError, Recipe
from rastro.domain.recipe_compare import RepairPolicy
from rastro.domain.recipes import CssRecipe, Pagination
from rastro.domain.schedule import ScheduleSpec
from rastro.domain.selector_policy import check_untrusted_field
from rastro.studio import files

_NEXT_TABLE = re.compile(r"(?m)^[ \t]*\[")


def _format(path: Path) -> Literal["toml", "json"]:
    return "json" if path.suffix.lower() == ".json" else "toml"


def _read(path: Path) -> tuple[str, Job]:
    text = files.read_job_bytes(path).decode("utf-8")
    return text, parse_job(text, format=_format(path))


def _commit(path: Path, expected: str, changed: str, wanted: dict[str, Any]) -> str:
    """Reparse ``changed``; it must equal ``wanted`` exactly, then write it atomically."""
    try:
        written = parse_job(changed, format=_format(path))
    except (ValueError, ConfigurationError):
        raise ConfigurationError(
            "Studio could not write this property; edit the job by hand"
        ) from None
    if written.model_dump(mode="json") != wanted:
        raise ConfigurationError("The edit changed more than the requested property; edit by hand")
    validate_job_paths(written, base_dir=path.parent)
    raw = changed.encode("utf-8")
    files.write_atomic(path, raw, expected_digest=expected)
    return files.digest(raw)


def save_pagination(path: Path, expected: str, source: int | None, pagination: Pagination) -> str:
    """Set the next-link expression and page cap on the job recipe or ``sources[source]``."""
    text, job = _read(path)
    if source is not None and not 0 <= source < len(job.sources):
        raise ConfigurationError("Select a recipe declared in the job")
    recipe = job.recipe if source is None else job.sources[source].recipe
    if recipe is None:
        raise ConfigurationError("Select a recipe declared in the job")
    if isinstance(recipe, CssRecipe):  # Studio edits are untrusted input (selector policy).
        check_untrusted_field(pagination.next)
    updated = Recipe.model_validate({**recipe_table(recipe), "pagination": pagination.model_dump()})
    wanted = job.model_dump(mode="json")
    holder = wanted if source is None else wanted["sources"][source]
    holder["recipe"] = updated.model_dump(mode="json")
    if _format(path) == "toml":
        newline = "\r\n" if "\r\n" in text else "\n"
        changed = replace_recipe(text.replace("\r\n", "\n"), updated, source=source)
        changed = changed.replace("\n", newline)
    else:
        data = json.loads(text)
        target = data if source is None else data["sources"][source]
        if not isinstance(target, dict):
            raise ConfigurationError("Write this source as an object before editing its recipe")
        target["recipe"] = recipe_table(updated)
        changed = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _commit(path, expected, changed, wanted)


def save_auto_promote(path: Path, expected: str, enabled: bool) -> str:
    """Write ``[repair] auto_promote`` on the operator's job; every gate still applies."""
    _, job = _read(path)
    policy = (job.repair or RepairPolicy()).model_copy(update={"auto_promote": enabled})
    wanted = {**job.model_dump(mode="json"), "repair": policy.model_dump(mode="json")}
    return _edit(path, expected, "repair", {"auto_promote": enabled}, wanted)


def _toml_table(text: str, table: str, values: dict[str, Any], *, replace: bool = False) -> str:
    """Set keys in an ordinary ``[table]`` (or rewrite it); unusual layouts fail the comparison."""
    lines = {key: f"{key} = {json.dumps(value)}" for key, value in values.items()}
    header = re.search(rf"(?m)^\[{re.escape(table)}\][ \t]*(?:#.*)?$", text)
    if header is None:
        separator = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{separator}\n[{table}]\n" + "\n".join(lines.values()) + "\n"
    following = _NEXT_TABLE.search(text, header.end())
    stop = following.start() if following else len(text)
    section = "\n" + "\n".join(lines.values()) + "\n\n" if replace else text[header.end() : stop]
    for key, line in () if replace else lines.items():
        key_line = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$")
        found = key_line.search(section)
        section = (
            section[: found.start()] + line + section[found.end() :]
            if found
            else f"\n{line}{section}"
        )
    return text[: header.end()] + section + text[stop:]


def _edit(
    path: Path,
    expected: str,
    table: str,
    values: dict[str, Any],
    wanted: dict[str, Any],
    *,
    replace: bool = False,
) -> str:
    text = files.read_job_bytes(path).decode("utf-8")
    if _format(path) == "toml":
        newline = "\r\n" if "\r\n" in text else "\n"
        changed = _toml_table(text.replace("\r\n", "\n"), table, values, replace=replace)
        changed = changed.replace("\n", newline)
    else:
        data = json.loads(text)
        holder = {} if replace else data.setdefault(table, {})
        if not isinstance(holder, dict):
            raise ConfigurationError(f"The job's {table} block must be a table")
        data[table] = {**holder, **values}
        changed = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _commit(path, expected, changed, wanted)


def save_robots(path: Path, expected: str, mode: Literal["obey", "ignore"]) -> str:
    """Write ``[acquisition] robots``; the run still needs the operator's grant for jobs
    that did not come from the operator."""
    _, job = _read(path)
    if job.acquisition is None:
        raise ConfigurationError("This job has no [acquisition] block to change")
    policy = job.acquisition.model_copy(update={"robots": mode})
    wanted = {**job.model_dump(mode="json"), "acquisition": policy.model_dump(mode="json")}
    return _edit(path, expected, "acquisition", {"robots": mode}, wanted)


def save_schedule(path: Path, expected: str, every: str, at: str | None) -> str:
    """Write the job's ``[schedule]``: how often, and an optional time of day."""
    _, job = _read(path)
    spec = {"every": every, **({"at": at} if at else {})}
    kept = job.schedule.jitter if job.schedule and job.schedule.jitter else None
    if kept:
        spec["jitter"] = kept
    wanted = {
        **job.model_dump(mode="json"),
        "schedule": ScheduleSpec(**spec).model_dump(mode="json"),
    }
    return _edit(path, expected, "schedule", spec, wanted, replace=True)
