"""Policy narrowing and failure shapes shared by the browser acquirer's page and scroll steps."""

from __future__ import annotations

from urllib.parse import urlsplit

from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import Outcome, Source


def page_policy(base: AcquisitionPolicy, url: str) -> AcquisitionPolicy:
    """An https page never lets its browser fall back to http; an http page keeps the grant."""
    return base.model_copy(
        update={"allow_http": base.allow_http and urlsplit(url).scheme == "http"}
    )


def canonical_url(url: str, policy: AcquisitionPolicy) -> str:
    """The form ``scroll`` compares: what ``permitted_url`` makes of the rendered URL."""
    try:
        return permitted_url(url, policy)[0]
    except AcquisitionError:
        return ""  # never equals a permitted URL, so no scroll follows it


def scroll_failure(
    source: Source, status: Outcome, code: str, requests: int = 0
) -> AcquisitionResult:
    return AcquisitionResult(
        source.url, None, status, code, egress=source.egress, requests=requests
    )
