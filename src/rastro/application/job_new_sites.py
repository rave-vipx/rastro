"""Draft a job from a research goal or from site URLs, fetching only approved hosts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import rastro.application.source_proposals as sp
from rastro.application.engine import Rastro
from rastro.application.field_proposals import _TYPED, ProposedFields, propose_fields
from rastro.application.job_new import (
    GoalJobResult,
    _validate_target,
    write_validated_job,
)
from rastro.application.job_render import render_job
from rastro.application.model_choice import ModelChoice
from rastro.application.navigation_offer import exploration_offer
from rastro.domain.job_fields import JobField, StringField, build_job_schema
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.models import ConfigurationError, Outcome, Source
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.search import SearchProvider


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
