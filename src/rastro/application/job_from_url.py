"""Build a verified job from a URL (N1, JOB-001).

Studio and callers fetch once through the acquisition port, suggest a recipe from
examples, and write a ``kind = "url"`` source so later runs scrape that URL again.
The fetch keeps ``Source.url`` (query included) and validates with ``permitted_url``
plus the operator's network grants. Diagnostics never echo the query string.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from rastro.application.destinations import permitted_url, public_address
from rastro.application.job_new import (
    _typed_fields,
    _validate_target,
    render_job,
    write_validated_job,
)
from rastro.application.model_choice import ModelChoice
from rastro.application.operator_trust import current_trust
from rastro.application.robots_cache import robots_cache
from rastro.application.source_proposals import default_acquirer
from rastro.application.suggest import suggest
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.acquisition_base import exact_host
from rastro.domain.job_fields import JobField, StringField
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.models import ConfigurationError, Limits, Outcome, Source
from rastro.domain.suggest import Example, check_examples
from rastro.ports.acquisition import AcquisitionPort


@dataclass(frozen=True)
class FetchedUrl:
    """One successful Studio/URL fetch; ``final_url`` keeps the query string."""

    requested: str
    final_url: str
    html: str
    policy: AcquisitionPolicy

    @property
    def allow_http(self) -> bool:
        return self.policy.allow_http


def _refuse(error: AcquisitionError | ValidationError | ValueError) -> ConfigurationError:
    """Map a destination failure to a configuration error without echoing the URL."""
    if isinstance(error, AcquisitionError):
        return ConfigurationError(f"Could not use that URL ({error.code})")
    return ConfigurationError("URL must be a valid HTTP(S) address")


def prepare_url_policy(url: str) -> tuple[str, AcquisitionPolicy]:
    """Canonicalise ``url`` (query kept) and build a policy with the operator's grants."""
    try:
        canonical = Source(url=url.strip()).url
        host = exact_host(urlsplit(canonical).hostname or "")
    except (ValidationError, ValueError) as err:
        raise _refuse(err) from None
    grants = current_trust().network_grants()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        try:
            public_address(host, allow_private=grants.private_network)
        except AcquisitionError as err:
            raise _refuse(err) from None
    allow_http = urlsplit(canonical).scheme == "http"
    policy = AcquisitionPolicy(allowed_hosts=(host,), allow_http=allow_http).with_grants(grants)
    try:
        permitted_url(canonical, policy)
    except AcquisitionError as err:
        raise _refuse(err) from None
    return canonical, policy


def html_from_url(url: str, *, acquisition: AcquisitionPort | None = None) -> FetchedUrl:
    """Fetch one URL under the operator's grants. Returns body, final URL and policy."""
    canonical, policy = prepare_url_policy(url)
    acquirer = acquisition or default_acquirer(policy)
    max_b = Limits().max_html_bytes

    async def _fetch() -> FetchedUrl:
        result = await acquirer.acquire(
            Source(url=canonical), max_bytes=max_b, representation="html"
        )
        if result.status is Outcome.SUCCESS and result.body:
            # Prefer the transport's final URL when present; keep the query either way.
            final = result.url or canonical
            try:
                final = Source(url=final).url
            except ValidationError:
                final = canonical
            return FetchedUrl(
                requested=url.strip(), final_url=final, html=result.body, policy=policy
            )
        outcome = result.status if result.status is not None else Outcome.INSUFFICIENT_EVIDENCE
        raise ConfigurationError(f"Could not fetch the URL (outcome: {outcome})")

    with robots_cache(), asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_fetch())


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
    fetched: FetchedUrl | None = None,
) -> tuple[Path, bool]:
    """Fetch ``url`` (or reuse ``fetched``), verify a recipe, and write a URL job.

    Returns the written path and whether the job sets ``allow_http``.
    """
    opts = {"dataset": dataset, "identity": identity, "recipe_memory": recipe_memory}
    _validate_target(path, None, opts)
    page = fetched if fetched is not None and fetched.requested == url.strip() else None
    if page is None:
        page = html_from_url(url, acquisition=acquisition)
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
        page.html,
        job_fields,
        examples=checked,
        url=page.final_url,
        records_min=records_min,
        model=model,
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
    source = UrlJobSource(url=page.final_url)
    typed = _typed_fields(page.html, job_fields, checked, found, Source(url=page.final_url))
    # Persist the serialisable policy (grants are process-local and must not be written).
    written_policy = AcquisitionPolicy(
        allowed_hosts=page.policy.allowed_hosts, allow_http=page.policy.allow_http
    )
    written = write_validated_job(
        path, render_job(typed, found.recipe, (source,), policy=written_policy, **opts)
    )
    return written, page.allow_http
