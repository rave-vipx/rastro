"""Open one job page in a guarded browser, replay page steps and read what it shows.

Used by the agent browser tools and ``rastro shell``. Every call is one fresh source session
through the job's own browser acquisition: capability gate for the current input origin
(ADR 0037), host allowlist, pinned DNS and SSRF checks, robots, request budget and the
``NetworkGuard`` routes. Actions are the closed page-step vocabulary; nothing evaluates code
supplied by the caller. A plain HTTP source opens in the plain browser, which needs no grant;
screenshots and network capture need their grants. Nothing is kept between calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from rastro.application.browser_acquisition import BrowserAcquirer
from rastro.application.browser_policy import canonical_url
from rastro.application.browser_sessions import source_session
from rastro.application.capability_gate import require, require_capabilities
from rastro.application.destinations import permitted_url
from rastro.application.network_recipes import collecting
from rastro.application.robots_cache import robots_cache
from rastro.domain.acquisition import AcquisitionError
from rastro.domain.capability_grants import Capability
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, Limits, Outcome, Source
from rastro.domain.page_steps import PageStep, PageSteps, steps_capabilities
from rastro.ports.engine import CapturedResponse, NetworkCapture

__all__ = ["LivePage", "PageRefused", "engine_parts", "load_live_page", "open_live_page"]

_STEPS: TypeAdapter[tuple[PageStep, ...]] = TypeAdapter(PageSteps)
ANY_PATH = ".*"
"""The capture pattern used when the caller asks for network responses and the job has none."""


@dataclass(frozen=True)
class LivePage:
    """What the page showed after its steps: final URL, document and requested evidence."""

    url: str
    html: str
    screenshot: bytes | None = field(default=None, repr=False)
    responses: tuple[CapturedResponse, ...] = ()


class PageRefused(ConfigurationError):
    """The page or a step was refused or failed; carries a safe code only, never a URL."""

    error_code = "page_refused"

    def __init__(self, code: str) -> None:
        super().__init__(
            f"The page could not be opened or acted on ({code})",
            location="url",
            hint="Stay on the job's allowed hosts; check the steps' selectors.",
        )
        self.code = code


class _Evidence:
    """Wraps the renderer so the screenshot a session render took is kept: the acquirer
    returns the document only. Everything else is the renderer's own."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.screenshot = inner, None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def render(self, target: Any, **options: Any) -> Any:
        result = await self.inner.render(target, **options)
        self.screenshot = result.screenshot
        return result

    @asynccontextmanager
    async def session(self, targets: Any, *, policy: Any) -> AsyncIterator[Any]:
        async with self.inner.session(targets, policy=policy) as session:
            render = session.render

            async def keeping(target: Any, **options: Any) -> Any:
                result = await render(target, **options)
                self.screenshot = result.screenshot
                return result

            session.render = keeping  # the same session object: its protocols still match
            yield session


def parse_steps(raw: Sequence[Any]) -> tuple[PageStep, ...]:
    """Validate caller-supplied steps against the closed page-step vocabulary."""
    return _STEPS.validate_python(tuple(raw))


def engine_parts(mode: str) -> tuple[Any, Any, Any]:
    """The renderer for ``mode``, an HTTP transport (robots) and a resolver."""
    from rastro.adapters.dns import SystemResolver
    from rastro.adapters.http import CurlTransport
    from rastro.browser import renderer_for

    return renderer_for(mode), CurlTransport(), SystemResolver()


def url_source(job: Job, index: int) -> UrlJobSource:
    if isinstance(index, bool) or not 1 <= index <= len(job.sources):
        raise ConfigurationError("source is out of range", location="source")
    item = job.sources[index - 1]
    if not isinstance(item, UrlJobSource) or job.acquisition is None:
        raise ConfigurationError("The source must be a URL source", location="sources")
    return item


def _acquirer(
    job: Job, item: UrlJobSource, *, screenshot: bool, network: bool
) -> tuple[BrowserAcquirer, _Evidence]:
    assert job.acquisition is not None
    require_capabilities(job)  # the job itself, for the origin it came from
    wanted: set[Capability] = set()
    if screenshot:
        wanted.add("screenshots")
    if network:
        wanted.add("network_capture")
    require(frozenset(wanted))
    mode = item.render if "render" in item.model_fields_set else job.acquisition.render
    mode = "browser" if mode == "http" else mode
    controls = job.acquisition.browser
    controls = controls.model_copy(
        update={
            "screenshot": controls.screenshot or screenshot,
            "capture_network": controls.capture_network or ((ANY_PATH,) if network else ()),
        }
    )
    policy = job.acquisition.model_copy(update={"render": mode, "browser": controls})
    inner, transport, resolver = engine_parts(mode)
    renderer = _Evidence(inner)
    acquirer = BrowserAcquirer(policy, renderer=renderer, transport=transport, resolver=resolver)
    return acquirer, renderer


async def open_live_page(
    job: Job,
    index: int = 1,
    *,
    url: str | None = None,
    steps: tuple[PageStep, ...] = (),
    screenshot: bool = False,
    network: bool = False,
) -> LivePage:
    """Render source ``index`` (or ``url`` on an allowed host), run ``steps``, read the page."""
    item = url_source(job, index)
    require(steps_capabilities(steps))  # before anything opens
    acquirer, renderer = _acquirer(job, item, screenshot=screenshot, network=network)
    source = (item.region or job.region).to_source(url or item.url)
    max_bytes = (job.limits or Limits()).max_input_bytes("html")
    try:
        permitted_url(source.url, acquirer.policy)
        with robots_cache(), collecting() as run:
            async with source_session() as scope:
                page = await acquirer.acquire(source, max_bytes=max_bytes)
                if page.status != Outcome.SUCCESS or page.body is None:
                    raise PageRefused(page.issue_code or "page_failed")
                responses = tuple(run)
                if steps:
                    after = Source(url=page.url, egress=page.egress)
                    page = await acquirer.run_steps(after, steps, max_bytes=max_bytes)
                    shown = scope.showing(
                        renderer,
                        host=(urlsplit(page.url).hostname or "").lower(),
                        url=canonical_url(page.url, acquirer.policy),
                    )
                    responses = shown.captured() if isinstance(shown, NetworkCapture) else ()
    except AcquisitionError as error:
        raise PageRefused(error.code) from None
    if page.body is None:
        raise PageRefused(page.issue_code or "page_failed")
    shot = (page.screenshot or renderer.screenshot) if screenshot else None
    return LivePage(page.url, page.body, shot, responses if network else ())


def load_live_page(job: Job, index: int = 1, **options: Any) -> LivePage:
    """``open_live_page`` from synchronous code (a CLI command or an MCP worker thread)."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(open_live_page(job, index, **options))
