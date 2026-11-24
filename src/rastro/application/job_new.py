"""Create a declarative job file from a saved page, sites, or a goal (JOB-001, DISC-001..003)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

import rastro.application.source_proposals as sp
from rastro.application.engine import Rastro
from rastro.application.field_proposals import _TYPED, FieldProposal
from rastro.application.job_loader import _read_local_source
from rastro.application.job_render import render_job as render_job  # re-exported
from rastro.application.model_choice import ModelChoice
from rastro.application.navigation_offer import ExplorationOffer
from rastro.application.suggest import suggest
from rastro.domain.job_fields import JobField, StringField, build_job_schema
from rastro.domain.job_sources import FileJobSource, validate_job_path_syntax
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError, Contract, Limits, Outcome, Source
from rastro.domain.suggest import Example, check_examples, infer_field_type


class GoalJobResult(Contract):
    """Outcome of starting a job from a goal or known sites."""

    created: Path | None = None
    allowed_hosts: tuple[str, ...] = ()
    proposals: tuple[sp.SourceProposal, ...] = ()
    fields: tuple[FieldProposal, ...] = ()
    preview: tuple[dict[str, Any], ...] = ()
    status: Outcome = Outcome.SUCCESS
    exploration: ExplorationOffer | None = None
    """Set when the first page may hide records behind clicks; ``explore_job`` can find them."""


def _validate_target(job: Path, page: Path | None, opts: dict[str, Any]) -> None:
    ds, ident, mem = (opts.get(k) for k in ("dataset", "identity", "recipe_memory"))
    if job.suffix != ".toml":
        raise ConfigurationError("The new job file must end in .toml")
    if job.exists() or job.is_symlink():
        raise ConfigurationError("Job file already exists; refusing to overwrite it")
    if ds is not None and not ident:
        raise ConfigurationError("--dataset requires --identity naming the record key fields")
    if mem is not None:
        msg = "recipe_memory must be a file name relative to the job"
        try:
            validate_job_path_syntax(mem)
        except ValueError:
            raise ConfigurationError(msg) from None
        if PurePosixPath(mem).is_absolute() or PureWindowsPath(mem).anchor:
            raise ConfigurationError(msg)
        if not (job.parent / mem).parent.is_dir():
            raise ConfigurationError("The recipe_memory folder must already exist")
    base = job.parent.resolve()
    taken = {str((base / job.name).resolve()).casefold(): "the job file"}
    if page is not None:
        taken[str(page.resolve()).casefold()] = "the page"
    for label, name in (("dataset", ds), ("recipe_memory", mem)):
        if name is not None:
            resolved = str((base / name).resolve()).casefold()
            if resolved in taken:
                raise ConfigurationError(f"{label} is the same file as {taken[resolved]}")
            taken[resolved] = f"the {label}"


def write_validated_job(path: Path, text: str) -> Path:
    parse_job(text, format="toml")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


def new_job(
    path: Path,
    *,
    page: Path,
    examples: Sequence[Example] = (),
    url: str | None = None,
    records_min: int = 2,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    allow_partial: bool = False,
    recipe_memory: str | None = None,
    fields: Sequence[str] = (),
    model: ModelChoice = None,
) -> Path:
    """Write a new TOML job at ``path``; never overwrite an existing file.

    The suggested recipe's preview must succeed; ``allow_partial=True`` writes a job whose
    preview is only a partial success. ``recipe_memory`` names a recipe store relative to
    the job file, in a folder that already exists; it enables repair and promotion.
    With ``model``, examples are optional: ``fields`` names the fields to extract and
    the model proposes the recipe, verified like an example-built one.
    """
    opts = {"dataset": dataset, "identity": identity, "recipe_memory": recipe_memory}
    try:
        rel = page.resolve().relative_to(path.parent.resolve()).as_posix()
        rel_page = validate_job_path_syntax(rel)
    except ValueError:
        if not path.name.endswith(".toml") or path.exists() or path.is_symlink():
            _validate_target(path, None, opts)
        raise ConfigurationError("The page must be inside the new job file's directory") from None
    _validate_target(path, page, opts)
    html = _read_local_source(page, Limits().max_html_bytes, "html")
    checked = check_examples(examples) if examples or model is None else ()
    names = dict.fromkeys([*(k for e in checked for k in e), *fields])
    if not names:
        raise ConfigurationError("Name the fields with examples or --field")
    try:
        job_fields: list[JobField] = [StringField(name=name) for name in names]
    except ValidationError:
        msg = "Example field names must start with a letter and use letters, digits or _"
        raise ConfigurationError(msg) from None
    source = FileJobSource(path=rel_page, url=url)
    found = suggest(
        html, job_fields, examples=checked, url=url, records_min=records_min, model=model
    )
    if found.status != Outcome.SUCCESS and not allow_partial:
        issues = ", ".join(
            sorted({f"{i.code}:{i.field}" if i.field else i.code for i in found.preview.issues})
        )
        raise ConfigurationError(
            f"The suggested recipe's preview is {found.status}, not success"
            + (f" ({issues})" if issues else "")
            + "; add examples, or pass --allow-partial to write the job anyway"
        )
    src_obj = Source(url=source.resolved_source_url())
    typed = _typed_fields(html, job_fields, checked, found, src_obj)
    return write_validated_job(path, render_job(typed, found.recipe, source, **opts))


def _typed_fields(
    html: str, fields: list[JobField], ex: Any, found: Any, src: Source
) -> list[JobField]:
    client, base, typed = Rastro(), (found.preview.status, len(found.preview.records)), list(fields)
    for idx, f in enumerate(fields):
        if (kind := infer_field_type([e[f.name] for e in ex if f.name in e])) != "string":
            trial = [*typed[:idx], _TYPED[kind](name=f.name), *typed[idx + 1 :]]
            res = client.extract_html(
                html, source=src, schema=build_job_schema(trial), recipe=found.recipe
            )
            if (res.status, len(res.records)) == base:
                typed = trial
    return typed
