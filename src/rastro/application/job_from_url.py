"""Build a verified job from a public URL (N1, JOB-001).

Studio and callers fetch once through the acquisition port, suggest a recipe from
examples, and write a ``kind = "url"`` source so later runs scrape that URL again.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

import rastro.application.source_proposals as sp
from rastro.application.job_new import (
    _typed_fields,
    _validate_target,
    _write_validated_job,
    render_job,
)
from rastro.application.model_choice import ModelChoice
from rastro.application.suggest import suggest
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.job_fields import JobField, StringField
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.models import ConfigurationError, Outcome, Source
from rastro.domain.suggest import Example, check_examples
from rastro.ports.acquisition import AcquisitionPort


def html_from_url(
    url: str, *, acquisition: AcquisitionPort | None = None
) -> tuple[str, str, AcquisitionPolicy]:
    """Fetch one public URL. Returns ``(final_url, html, policy)`` for a successful body."""
    proposal = sp.clean_proposal(url.strip(), "", "search")
    if proposal is None:
        raise ConfigurationError("URL must be a valid public HTTP(S) address")
    _hosts, policy, results = sp.acquire_approved_pages(
        [proposal.url], [proposal.host], acquisition=acquisition
    )
    for result in results:
        if result.status is Outcome.SUCCESS and result.body:
            return result.url, result.body, policy
    outcome = results[-1].status if results else Outcome.INSUFFICIENT_EVIDENCE
    raise ConfigurationError(f"Could not fetch the URL (outcome: {outcome})")


def new_job_from_url(
    path: Path,
    *,
    url: str,
    examples: Sequence[Example] = (),
    records_min: int = 2,
    dataset: str | None = None,
    identity: Sequence[str] = (),
    allow_partial: bool = False,
    recipe_memory: str | None = None,
    fields: Sequence[str] = (),
    model: ModelChoice = None,
    acquisition: AcquisitionPort | None = None,
) -> Path:
    """Fetch ``url``, verify a recipe, and write a job whose source is that URL."""
    opts = {"dataset": dataset, "identity": identity, "recipe_memory": recipe_memory}
    _validate_target(path, None, opts)
    final_url, html, policy = html_from_url(url, acquisition=acquisition)
    checked = check_examples(examples) if examples or model is None else ()
    names = dict.fromkeys([*(k for e in checked for k in e), *fields])
    if not names:
        raise ConfigurationError("Name the fields with examples or --field")
    try:
        job_fields: list[JobField] = [StringField(name=name) for name in names]
    except ValidationError:
        msg = "Example field names must start with a letter and use letters, digits or _"
        raise ConfigurationError(msg) from None
    found = suggest(
        html, job_fields, examples=checked, url=final_url, records_min=records_min, model=model
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
    source = UrlJobSource(url=final_url)
    typed = _typed_fields(html, job_fields, checked, found, Source(url=final_url))
    return _write_validated_job(
        path, render_job(typed, found.recipe, (source,), policy=policy, **opts)
    )
