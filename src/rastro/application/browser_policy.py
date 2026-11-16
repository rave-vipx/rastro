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


def refused_step(
    result: AcquisitionResult, policy: AcquisitionPolicy, max_bytes: int, *, page: str | None = None
) -> AcquisitionError | None:
    """The error for a finished browser step that must not be used, else ``None``.

    Every refusal carries ``result.requests``: the step's transfers happened, so the
    caller charges them whatever becomes of the document.
    """
    code, outcome = None, Outcome.PERMANENT_FAILURE
    if page is not None and canonical_url(result.url, policy) != page:
        code = "scroll_navigated"  # another document was never checked against robots
    elif result.status != Outcome.SUCCESS:
        code, outcome = result.issue_code or "empty_document", result.status
    elif not result.body:
        code, outcome = "empty_document", Outcome.INVALID_DATA
    elif len(result.body.encode("utf-8")) > max_bytes:
        code = "response_too_large"
    return None if code is None else AcquisitionError(code, outcome, requests=result.requests)
