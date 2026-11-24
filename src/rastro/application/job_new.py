"""Create a declarative job file from a saved page, sites, or a goal (JOB-001, DISC-001..003)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import ValidationError

import rastro.application.source_proposals as sp
from rastro.application.engine import Rastro
from rastro.application.field_proposals import _TYPED, FieldProposal, ProposedFields, propose_fields
from rastro.application.job_loader import _read_local_source
from rastro.application.job_render import render_job as render_job  # re-exported
from rastro.application.model_choice import ModelChoice
from rastro.application.navigation_offer import ExplorationOffer, exploration_offer
from rastro.application.suggest import suggest
from rastro.domain.job_fields import JobField, StringField, build_job_schema
from rastro.domain.job_sources import FileJobSource, UrlJobSource, validate_job_path_syntax
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError, Contract, Limits, Outcome, Source
from rastro.domain.suggest import Example, check_examples, infer_field_type
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.search import SearchProvider


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


def new_job_from_goal(
    path: Path | None = None,
    *,
    goal: str,
    approve: Sequence[str] = (),
    search: SearchProvider | Literal["searxng", "model"] | None = None,
    provider: SearchProvider | None = None,
    model: ModelChoice = None,
    acquisition: AcquisitionPort | None = None,
    allow_remote: bool = False,
    fields: Sequence[str] = (),
    records_min: int = 2,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    allow_partial: bool = False,
    recipe_memory: str | None = None,
) -> GoalJobResult:
    """Propose sources for ``goal``; with ``approve`` hosts, also write a verified job.

    ``search`` picks SearXNG or the model to find sources, ``provider`` supplies a search
    provider object, and ``model`` serves source search or field proposals. Without
    ``approve`` nothing is fetched or written. ``approve`` names the only hosts fetched;
    ``acquisition`` replaces the default HTTP acquirer and ``allow_remote`` lets a hosted
    SearXNG or model receive data. ``fields`` names the fields to propose, ``records_min``
    is the minimum records, ``allow_partial`` keeps pages whose preview is partial, and
    ``dataset``, ``identity`` and ``recipe_memory`` are as in ``new_job``.
    """
    s_model = model if search == "model" or (search is None and provider is None) else None
    proposals = sp.propose_sources(
        goal, search=search, provider=provider, model=s_model, allow_remote=allow_remote
    )
    if not approve:
        return GoalJobResult(created=None, proposals=proposals)
    if path is None:
        raise ConfigurationError("Pass -o FILE.toml to write the approved job")
    acq = sp.acquire_approved_pages([p.url for p in proposals], approve, acquisition=acquisition)
    return _build_approved_job(
        path,
        acq,
        proposals,
        fields=fields,
        records_min=records_min,
        model=model,
        allow_remote=allow_remote,
        dataset=dataset,
        identity=identity,
        allow_partial=allow_partial,
        recipe_memory=recipe_memory,
    )


def new_job_from_sites(
    path: Path,
    *,
    sites: Sequence[str],
    model: ModelChoice = None,
    acquisition: AcquisitionPort | None = None,
    allow_remote: bool = False,
    fields: Sequence[str] = (),
    records_min: int = 2,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    allow_partial: bool = False,
    recipe_memory: str | None = None,
) -> GoalJobResult:
    """Fetch only the given ``sites`` (their hosts are the approval) and write a verified job.

    ``model`` and ``allow_remote`` govern optional field proposals, ``acquisition``
    replaces the default HTTP acquirer, ``fields`` names the fields to propose,
    ``records_min`` is the minimum records, ``allow_partial`` keeps pages whose preview is
    partial, and ``dataset``, ``identity`` and ``recipe_memory`` are as in ``new_job``.
    """
    cleaned = [sp.clean_proposal(u.strip(), "", "search") for u in sites if u.strip()]
    if not cleaned or any(p is None for p in cleaned):
        raise ConfigurationError("Every --site must be a valid public HTTP(S) URL")
    valid = [p for p in cleaned if p is not None]
    acq = sp.acquire_approved_pages(
        [p.url for p in valid], [p.host for p in valid], acquisition=acquisition
    )
    return _build_approved_job(
        path,
        acq,
        tuple(valid),
        fields=fields,
        records_min=records_min,
        model=model,
        allow_remote=allow_remote,
        dataset=dataset,
        identity=identity,
        allow_partial=allow_partial,
        recipe_memory=recipe_memory,
    )


def _build_approved_job(
    path: Path, acq: tuple[Any, ...], proposals: tuple[sp.SourceProposal, ...], **opts: Any
) -> GoalJobResult:
    hosts, policy, results = acq
    _validate_target(path, None, opts)
    pages = [(r.url, r.body) for r in results if r.status is Outcome.SUCCESS and r.body]
    if not pages:
        outcome = results[-1].status if results else Outcome.INSUFFICIENT_EVIDENCE
        raise ConfigurationError(f"No approved source could be acquired (outcome: {outcome})")
    proposed: ProposedFields | None = None
    f_opts = {k: opts[k] for k in ("fields", "records_min", "model", "allow_remote") if k in opts}
    for page_url, html in pages:
        try:
            proposed = propose_fields(html, url=page_url, **f_opts)
            break
        except ConfigurationError:
            continue
    if proposed is None:
        raise ConfigurationError(f"No records observed (outcome: {Outcome.INSUFFICIENT_EVIDENCE})")
    job_fields: list[JobField] = [_TYPED.get(f.type, StringField)(name=f.name) for f in proposed]
    schema, client = build_job_schema(job_fields), Rastro()
    preview_rows, kept = list[dict[str, Any]](), list[UrlJobSource]()
    for page_url, html in pages:
        res = client.extract_html(
            html, source=Source(url=page_url), schema=schema, recipe=proposed.recipe
        )
        if res.records and (res.status is Outcome.SUCCESS or opts.get("allow_partial", False)):
            kept.append(UrlJobSource(url=page_url))
            preview_rows.extend(rec.data.model_dump(mode="json") for rec in res.records)
    if not preview_rows:
        raise ConfigurationError(f"No valid records (outcome: {Outcome.INSUFFICIENT_EVIDENCE})")
    write_validated_job(path, render_job(job_fields, proposed.recipe, kept, policy=policy, **opts))
    res_kw: dict[str, Any] = {
        "allowed_hosts": hosts,
        "proposals": proposals,
        "fields": tuple(proposed),
        "exploration": exploration_offer(
            pages[0][1], len(preview_rows), opts.get("records_min", 2), url=pages[0][0]
        ),
    }
    return GoalJobResult(created=path, preview=tuple(preview_rows), **res_kw)


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
