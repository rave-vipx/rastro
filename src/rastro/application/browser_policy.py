"""Policy narrowing and failure shapes shared by the browser acquirer's page and scroll steps."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

from rastro.application.destinations import permitted_url
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, AcquisitionResult
from rastro.domain.browser_controls import BrowserControls
from rastro.domain.models import ConfigurationError, Outcome, Source

PROFILE_DIR_ENV = "RASTRO_PROFILE_DIR"
BROWSER_REGION: ContextVar[tuple[str, str] | None] = ContextVar("browser_region", default=None)
"""The (locale, timezone) of the source being rendered, set from the job's [region]."""

_AD_HOSTS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
)


def host_matches(host: str, domains: tuple[str, ...]) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one."""
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def blocked_by_controls(url: str, controls: BrowserControls) -> bool:
    """Whether the controls' blocked domains (and the ad list, if asked) refuse this request."""
    host = urlsplit(url).hostname or ""
    blocked = (*controls.blocked_domains, *(_AD_HOSTS if controls.block_ads else ()))
    return host_matches(host, blocked)


def region_options() -> dict[str, str]:
    """Browser context locale and timezone for the current source; unknown values are left out."""
    locale, timezone = BROWSER_REGION.get() or ("und", "")
    return {
        **({"locale": locale} if locale and locale != "und" else {}),
        **({"timezone_id": timezone} if timezone else {}),
    }


def cdp_endpoint(name: str, environ: Mapping[str, str] | None = None) -> str:
    """The remote-browser endpoint held in the environment variable ``name``.

    The value is a credential-bearing URL: errors name the variable, never the value.
    """
    value = (os.environ if environ is None else environ).get(name, "")
    if not value:
        raise ConfigurationError(
            f"The remote browser endpoint variable {name} is not set",
            hint="Export it as a ws(s):// or http(s):// DevTools URL before the run.",
        )
    if urlsplit(value).scheme not in {"ws", "wss", "http", "https"}:
        raise ConfigurationError(f"{name} must hold a ws(s):// or http(s):// DevTools URL")
    return value


def profile_directory(name: str, environ: Mapping[str, str] | None = None) -> Path:
    """The operator-mapped directory of the persistent profile ``name``."""
    root = (os.environ if environ is None else environ).get(PROFILE_DIR_ENV, "")
    if not root:
        raise ConfigurationError(
            f"Persistent browser profiles need {PROFILE_DIR_ENV} to name their parent directory"
        )
    base = Path(root).resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise ConfigurationError("A session profile is a plain name inside the profile directory")
    return path


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
