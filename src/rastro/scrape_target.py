"""Turn a ``rastro.scrape`` target into one bounded document (INT-001, SEC-001, SEC-005).

A target is HTML text, a ``pathlib.Path`` or an ``http(s)://`` URL. Files are read
with a byte bound and never through a symlink. A URL is fetched once, under a
policy that allows only that URL's host; the response is then reused offline. A
refused URL climbs the acquisition ladder (ADR 0039) before it counts as blocked.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from rastro.application.acquisition_ladder import StepOutcome, climb_async, fetch_outcome, gate
from rastro.application.ladder_levels import Rung, ladder_rungs
from rastro.application.solver_plugins import any_solver_installed, solver_port
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import (
    ConfigurationError,
    Limits,
    Outcome,
    Representation,
    ResourceLimitError,
    Source,
)
from rastro.domain.source_notes import LadderStep
from rastro.ports.acquisition import AcquisitionPort

DEFAULT_URL = "https://localhost/"


@dataclass(frozen=True)
class Page:
    """The requested source and its document; ``document`` is None if fetching failed."""

    source: Source
    document: str | None
    representation: Representation = "html"
    fetched: AcquisitionResult | None = None
    policy: AcquisitionPolicy | None = None
    ladder: tuple[LadderStep, ...] = field(default=())
    """Every ladder level tried after a refusal; empty when the first fetch was not refused."""

    @property
    def final_source(self) -> Source:
        """The source the document came from, after any permitted redirect."""
        if self.fetched is None:
            return self.source
        return Source.model_validate({**self.source.model_dump(), "url": self.fetched.url})


class Prefetched:
    """An acquisition port that replays one completed fetch instead of fetching again."""

    def __init__(self, fetched: AcquisitionResult) -> None:
        self._fetched = fetched

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        return self._fetched


def is_url(target: object) -> bool:
    return isinstance(target, str) and target[:8].lower().startswith(("http://", "https://"))


async def load_page(
    target: str | Path,
    *,
    url: str | None,
    locale: str,
    limits: Limits,
    representation: Representation,
) -> Page:
    max_bytes = limits.max_input_bytes(representation)
    if isinstance(target, Path):
        text = read_bounded(target, max_bytes)
        return Page(_source(url or DEFAULT_URL, locale), text, representation)
    if not isinstance(target, str):
        raise ConfigurationError("target must be HTML text, a pathlib.Path or an http(s) URL")
    if not is_url(target):
        if looks_like_path(target):
            raise ConfigurationError(
                "A str target is document text; pass pathlib.Path(...) for files"
            )
        return Page(_source(url or DEFAULT_URL, locale), target, representation)
    if url is not None and url != target:
        raise ConfigurationError("url= is only for text and file targets; a URL target is the URL")
    source = _source(target, locale)
    parts = urlsplit(source.url)
    try:
        policy = AcquisitionPolicy(
            allowed_hosts=(parts.hostname or "",), allow_http=parts.scheme == "http"
        )
    except ValidationError:
        raise ConfigurationError("The target URL needs an exact host name") from None
    fetched, ladder = await _fetch(policy, source, max_bytes, representation)
    document = fetched.body if fetched.status == Outcome.SUCCESS else None
    return Page(source, document, representation, fetched, policy, ladder)


async def _fetch(
    policy: AcquisitionPolicy, source: Source, max_bytes: int, representation: Representation
) -> tuple[AcquisitionResult, tuple[LadderStep, ...]]:
    """Fetch at the job's own level; on a refusal, climb until a level returns the page."""
    first = await web_acquirer(policy).acquire(
        source, max_bytes=max_bytes, representation=representation
    )
    if fetch_outcome(first) != "blocked":
        return first, ()
    rungs = ladder_rungs(
        policy, html=representation == "html", rotate=False, solvers=any_solver_installed()
    )

    async def attempt(rung: Rung) -> tuple[AcquisitionResult, StepOutcome]:
        fetched = await ladder_port(rung).acquire(
            source, max_bytes=max_bytes, representation=representation
        )
        return fetched, fetch_outcome(fetched)

    if len(rungs) < 2:
        return first, (LadderStep(level=rungs[0].level, outcome="blocked"),)
    climbed, steps, _ = await climb_async(rungs[1:], rungs[1].level, attempt, gate)
    return climbed or first, (LadderStep(level=rungs[0].level, outcome="blocked"), *steps)


def ladder_port(rung: Rung) -> AcquisitionPort:
    """The port for one ladder level above plain HTTP."""
    if rung.level == "solver_plugin":
        port = solver_port(rung.policy)
        if port is None:
            raise ConfigurationError("No solver plugin is installed")
        return port
    if rung.policy.render == "http":
        return web_acquirer(rung.policy)
    try:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.browser_acquisition import BrowserAcquirer
        from rastro.browser import _renderer as renderer_for

        renderer = renderer_for(rung.policy.render)
    except ModuleNotFoundError:
        raise ConfigurationError("This level needs rastro-engine[browser]") from None
    return BrowserAcquirer(
        rung.policy, renderer=renderer, transport=CurlTransport(), resolver=SystemResolver()
    )


def looks_like_path(target: str) -> bool:
    """A single token with no markup or JSON delimiters, such as ``page.html``."""
    text = target.strip()
    return (
        0 < len(text) <= 1024
        and not any(char in text for char in '<>{}[]"')
        and not any(char.isspace() for char in text)
        and ("." in text or "/" in text or "\\" in text)
    )


def web_acquirer(policy: AcquisitionPolicy) -> AcquisitionPort:
    """Build the HTTP acquirer; the `http` extra is required only for URL targets."""
    try:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer
    except ModuleNotFoundError as error:
        if error.name not in {"curl_cffi", "protego"}:
            raise
        raise ImportError("Scraping a URL requires: pip install 'rastro-engine[http]'") from None
    return WebAcquirer(policy, transport=CurlTransport(), resolver=SystemResolver())


def read_bounded(path: Path, max_bytes: int) -> str:
    """Read a regular UTF-8 file of at most ``max_bytes``; refuse symlinks."""
    if path.is_symlink():
        raise ConfigurationError("Target files must not be symlinks")
    try:
        # O_NONBLOCK: opening a FIFO with no writer returns at once, then fails the type check.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
    except OSError:
        raise ConfigurationError("Target file cannot be opened") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ConfigurationError("Target path must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise ResourceLimitError("Target file exceeds the input byte limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("Target file must be valid UTF-8") from None


def _source(url: str, locale: str) -> Source:
    try:
        return Source(url=url, locale=locale)
    except ValidationError:
        raise ConfigurationError("url= must be an absolute http(s) URL, no credentials") from None
