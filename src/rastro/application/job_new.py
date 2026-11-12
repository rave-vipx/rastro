"""Create a declarative job file from a saved page and example values (JOB-001, DISC-003).

The written job holds a verified CSS recipe, a local file source and conservatively typed
fields: a field keeps a non-string type only when every previewed value validates with it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

from rastro.application.engine import Rastro
from rastro.application.job_loader import _read_local_source
from rastro.application.suggest import suggest
from rastro.domain.job_fields import (
    DateField,
    DecimalField,
    IntegerField,
    JobField,
    StringField,
    UrlField,
    build_job_schema,
)
from rastro.domain.job_sources import FileJobSource, validate_job_path_syntax
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError, CssRecipe, Limits, Outcome, Source
from rastro.domain.suggest import Example, Suggestion, check_examples, infer_field_type

_TYPED = {"integer": IntegerField, "decimal": DecimalField, "date": DateField, "url": UrlField}


def new_job(
    path: Path,
    *,
    page: Path,
    examples: Sequence[Example],
    url: str | None = None,
    records_min: int = 2,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    allow_partial: bool = False,
    recipe_memory: str | None = None,
) -> Path:
    """Write a new TOML job at ``path``; never overwrite an existing file.

    The suggested recipe's preview must succeed; ``allow_partial=True`` writes a job whose
    preview is only a partial success. ``recipe_memory`` names a recipe store relative to
    the job file, in a folder that already exists; it enables repair and promotion.
    """
    if path.suffix != ".toml":
        raise ConfigurationError("The new job file must end in .toml")
    if path.exists() or path.is_symlink():
        raise ConfigurationError("Job file already exists; refusing to overwrite it")
    if dataset is not None and not identity:
        raise ConfigurationError("--dataset requires --identity naming the record key fields")
    if recipe_memory is not None:
        _check_memory(recipe_memory, path.parent, dataset)
    page_path = _relative(page, path.parent)
    html = _read_local_source(page, Limits().max_html_bytes, "html")
    checked = check_examples(examples)
    try:
        fields: list[JobField] = [
            StringField(name=name) for name in dict.fromkeys(k for e in checked for k in e)
        ]
    except ValidationError:
        raise ConfigurationError(
            "Example field names must start with a letter and use letters, digits or _"
        ) from None
    source = FileJobSource(path=page_path, url=url)
    found = suggest(html, fields, examples=checked, url=url, records_min=records_min)
    if found.status != Outcome.SUCCESS and not allow_partial:
        issues = ", ".join(
            sorted({f"{i.code}:{i.field}" if i.field else i.code for i in found.preview.issues})
        )
        raise ConfigurationError(
            f"The suggested recipe's preview is {found.status}, not success"
            + (f" ({issues})" if issues else "")
            + "; add examples, or pass --allow-partial to write the job anyway"
        )
    typed = _typed_fields(html, fields, checked, found, Source(url=source.resolved_source_url()))
    text = render_job(
        typed,
        found.recipe,
        source,
        dataset=dataset,
        identity=identity,
        recipe_memory=recipe_memory,
    )
    parse_job(text, format="toml")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _check_memory(memory: str, base: Path, dataset: str | None) -> None:
    try:
        validate_job_path_syntax(memory)
    except ValueError:
        raise ConfigurationError("recipe_memory must be a file name relative to the job") from None
    if PurePosixPath(memory).is_absolute() or PureWindowsPath(memory).anchor:
        raise ConfigurationError("recipe_memory must be a file name relative to the job")
    if memory == dataset:
        raise ConfigurationError("recipe_memory must be a different file from the dataset")
    if not (base / memory).parent.is_dir():
        raise ConfigurationError("The recipe_memory folder must already exist")


def _relative(page: Path, base: Path) -> str:
    try:
        relative = page.resolve().relative_to(base.resolve())
    except ValueError:
        raise ConfigurationError("The page must be inside the new job file's directory") from None
    return validate_job_path_syntax(relative.as_posix())


def _typed_fields(
    html: str,
    fields: list[JobField],
    examples: Sequence[dict[str, str]],
    found: Suggestion[Any],
    source: Source,
) -> list[JobField]:
    client = Rastro()
    baseline = (found.preview.status, len(found.preview.records))
    typed = list(fields)
    for index, field in enumerate(fields):
        kind = infer_field_type([e[field.name] for e in examples if field.name in e])
        if kind == "string":
            continue
        trial = [*typed[:index], _TYPED[kind](name=field.name), *typed[index + 1 :]]
        result = client.extract_html(
            html, source=source, schema=build_job_schema(trial), recipe=found.recipe
        )
        if (result.status, len(result.records)) == baseline:
            typed = trial
    return typed


def _quote(value: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes; TOML also forbids raw DEL.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _dataset_name(path: str) -> str:
    stem = re.sub(r"[^a-z0-9_]", "_", Path(path).stem.lower())
    return (stem if stem[:1].isalpha() else f"data_{stem}")[:64]


def render_job(
    fields: Sequence[JobField],
    recipe: CssRecipe,
    source: FileJobSource,
    *,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    recipe_memory: str | None = None,
) -> str:
    """TOML text for a one-source job; field names are validated identifiers."""
    lines = [
        "# Created by `rastro new`. Review the recipe, then run: rastro job run <this file>",
        "format_version = 1",
        *([f"recipe_memory = {_quote(recipe_memory)}"] if recipe_memory is not None else []),
        "",
        "[recipe]",
        'strategy = "css"',
        f"record_selector = {_quote(recipe.record_selector)}",
        "",
        "[recipe.fields]",
        *(f"{name} = {_quote(selector)}" for name, selector in recipe.fields.items()),
        "",
    ]
    if dataset is not None:
        lines += [
            "[dataset]",
            f"name = {_quote(_dataset_name(dataset))}",
            f"path = {_quote(dataset)}",
            f"identity = [{', '.join(_quote(name) for name in identity)}]",
            "",
        ]
    lines += ["[[sources]]", 'kind = "file"', f"path = {_quote(source.path)}"]
    if source.url is not None:
        lines.append(f"url = {_quote(source.url)}")
    for field in fields:
        lines += ["", "[[fields]]", f"name = {_quote(field.name)}", f"type = {_quote(field.type)}"]
    return "\n".join(lines) + "\n"
