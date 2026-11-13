"""Change one field selector in a job file's text, then prove the result is the same job."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from typing import Any

from rastro.domain.jobs import Job, parse_job
from rastro.domain.models import ConfigurationError
from rastro.domain.selector_policy import check_untrusted_field

MAX_SELECTOR = 1024


@dataclass(frozen=True)
class SelectorEdit:
    """Replace the selector of ``field`` in ``sources[source].recipe`` (or the job recipe)."""

    field: str
    selector: str
    source: int | None = None


def _fields_of(raw: dict[str, Any], source: int | None) -> dict[str, Any]:
    try:
        holder = raw["recipe"] if source is None else raw["sources"][source]["recipe"]
        fields = holder["fields"]
    except (KeyError, IndexError, TypeError):
        raise ConfigurationError("The job has no recipe field mapping at that location") from None
    if not isinstance(fields, dict):
        raise ConfigurationError("The job has no recipe field mapping at that location")
    return fields


def list_selectors(job: Job) -> list[dict[str, Any]]:
    """Editable (source, field, selector) rows; source is null for the job-level recipe.

    JSON-LD pointer lists are not editable here and are left out."""
    rows: list[dict[str, Any]] = []
    if job.recipe is not None:
        rows += [
            {"source": None, "field": name, "selector": value}
            for name, value in job.recipe.fields.items()
            if isinstance(value, str)
        ]
    for index, source in enumerate(job.sources):
        recipe = getattr(source, "recipe", None)
        if recipe is not None:
            rows += [
                {"source": index, "field": name, "selector": value}
                for name, value in recipe.fields.items()
                if isinstance(value, str)
            ]
    return rows


def _replace_literal(text: str, old: str, new: str) -> str:
    quoted = [json.dumps(old, ensure_ascii=False)]
    if "'" not in old and "\n" not in old:
        quoted.append(f"'{old}'")
    present = [item for item in quoted if item in text]
    if sum(text.count(item) for item in present) != 1:
        raise ConfigurationError("The selector text is not unique in the file; edit it by hand")
    return text.replace(present[0], json.dumps(new, ensure_ascii=False))


def apply_edit(text: str, suffix: str, edit: SelectorEdit) -> str:
    """Return ``text`` with one selector replaced; TOML comments and layout are kept."""
    if not edit.selector or len(edit.selector) > MAX_SELECTOR or "\x00" in edit.selector:
        raise ConfigurationError("Selector must be non-empty text of at most 1024 characters")
    check_untrusted_field(edit.selector)
    json_file = suffix.lower() == ".json"
    raw = json.loads(text) if json_file else tomllib.loads(text)
    fields = _fields_of(raw, edit.source)
    old = fields.get(edit.field)
    if not isinstance(old, str):
        raise ConfigurationError("The job has no such field mapping")
    if json_file:
        fields[edit.field] = edit.selector
        return json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    new_text = _replace_literal(text, old, edit.selector)
    changed = tomllib.loads(new_text)
    if _fields_of(changed, edit.source).get(edit.field) != edit.selector:
        raise ConfigurationError("The edit did not change the intended selector")
    return new_text


def edited_job(text: str, suffix: str, edit: SelectorEdit) -> tuple[str, Job]:
    """Apply ``edit`` and validate the whole result as a job."""
    new_text = apply_edit(text, suffix, edit)
    return new_text, parse_job(new_text, format="json" if suffix.lower() == ".json" else "toml")
