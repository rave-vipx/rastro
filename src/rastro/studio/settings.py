"""N4/N12: change one operator-owned job property, reparse, compare, then replace atomically.

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
from rastro.domain.selector_policy import check_untrusted_field
from rastro.studio import files

_REPAIR_HEADER = re.compile(r"(?m)^\[repair\][ \t]*(?:#.*)?$")
_NEXT_TABLE = re.compile(r"(?m)^[ \t]*\[")
_AUTO_KEY = re.compile(r"(?m)^[ \t]*auto_promote[ \t]*=.*$")


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
    text, job = _read(path)
    policy = (job.repair or RepairPolicy()).model_copy(update={"auto_promote": enabled})
    wanted = {**job.model_dump(mode="json"), "repair": policy.model_dump(mode="json")}
    if _format(path) == "toml":
        newline = "\r\n" if "\r\n" in text else "\n"
        changed = _toml_auto_promote(text.replace("\r\n", "\n"), enabled)
        changed = changed.replace("\n", newline)
    else:
        data = json.loads(text)
        repair = data.setdefault("repair", {})
        if not isinstance(repair, dict):
            raise ConfigurationError("The job's repair block must be a table")
        repair["auto_promote"] = enabled
        changed = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _commit(path, expected, changed, wanted)


def _toml_auto_promote(text: str, enabled: bool) -> str:
    """Edit an ordinary ``[repair]`` table; unusual layouts fail the whole-job comparison."""
    line = f"auto_promote = {'true' if enabled else 'false'}"
    header = _REPAIR_HEADER.search(text)
    if header is None:
        separator = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{separator}\n[repair]\n{line}\n"
    following = _NEXT_TABLE.search(text, header.end())
    stop = following.start() if following else len(text)
    section = text[header.end() : stop]
    if _AUTO_KEY.search(section):
        section = _AUTO_KEY.sub(line, section, count=1)
    else:
        section = f"\n{line}{section}"
    return text[: header.end()] + section + text[stop:]
