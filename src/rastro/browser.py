"""Composition for job-selected HTTP, plain browser, and consented stealth acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from rastro.application.capability_gate import require_capabilities
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, Representation, Source
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.browser import BrowserRenderer, ScrollingAcquisition

if TYPE_CHECKING:
    from rastro.application.conditional import ConditionalRequests


def job_render_modes(job: Job) -> dict[str, str]:
    """Each declared URL's render mode, after the job's capabilities pass for its origin."""
    require_capabilities(job)  # first: no grant, no renderer or client
    modes: dict[str, str] = {}
    if job.acquisition is None:
        return modes
    for item in job.sources:
        if not isinstance(item, UrlJobSource):
            continue
        mode = item.render if "render" in item.model_fields_set else job.acquisition.render
        if item.url in modes and modes[item.url] != mode:
            raise ConfigurationError("Identical source URLs have conflicting render modes")
        modes[item.url] = mode
        if mode != "http" and (
            item.representation != "html"
            or (item.region or job.region).egress != "direct"
            or job.acquisition.egress_profiles
        ):
            raise ConfigurationError(
                "Browser modes require HTML and direct egress without profiles"
            )
    return modes


class _JobAcquisition:
    def __init__(
        self, modes: dict[str, str], clients: dict[str, AcquisitionPort], default: str = "http"
    ):
        self.modes, self.clients, self.default = modes, clients, default

    def mode(self, url: str) -> str:
        """The declared mode; else the one every declared source on the exact host shares."""
        if url in self.modes:
            return self.modes[url]
        host = urlsplit(url).hostname
        siblings = {mode for known, mode in self.modes.items() if urlsplit(known).hostname == host}
        return siblings.pop() if len(siblings) == 1 else self.default

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        return await self.clients[self.mode(source.url)].acquire(
            source, max_bytes=max_bytes, representation=representation
        )

    def renders_in_browser(self, url: str) -> bool:
        return self.mode(url) != "http"

    def may_render_in_browser(self, url: str) -> bool:
        """Links can lead to undeclared URLs (the default mode) or declared browser hosts."""
        return (
            self.mode(url) != "http"
            or self.default != "http"
            or any(mode != "http" for mode in self.modes.values())
        )

    def scrolls(self, url: str) -> bool:
        """Only plain browser sources scroll: Rastro does not drive scroll in a stealth page."""
        return self.mode(url) == "browser"

    def scroll_skip_code(self, url: str) -> str:
        stealth = self.mode(url) == "stealth"
        return "scroll_unsupported_stealth" if stealth else "scroll_ignored_http"

    async def scroll(self, source: Source, *, settle_ms: int, max_bytes: int) -> AcquisitionResult:
        client = self.clients[self.mode(source.url)]
        if not isinstance(client, ScrollingAcquisition):
            raise ConfigurationError("This source's acquisition cannot scroll")
        return await client.scroll(source, settle_ms=settle_ms, max_bytes=max_bytes)


def _renderer(mode: str) -> BrowserRenderer:
    """Import and build only the renderer this mode needs: stealth stays unbuilt unless asked."""
    if mode == "stealth":
        from rastro.adapters.scrapling_stealth import ScraplingStealth

        return ScraplingStealth()
    from rastro.adapters.playwright_browser import PlaywrightBrowser

    return PlaywrightBrowser()


def job_acquisition(
    job: Job,
    supplied: AcquisitionPort | None,
    *,
    conditional: ConditionalRequests | None = None,
) -> AcquisitionPort | None:
    """Route by render mode; ``conditional`` revalidates only the HTTP-mode sources."""
    modes = job_render_modes(job)
    if supplied is not None or not any(mode != "http" for mode in modes.values()):
        return supplied
    assert job.acquisition is not None
    renderers = {
        mode: _renderer(mode) for mode in {*modes.values(), job.acquisition.render} - {"http"}
    }
    from rastro.adapters.dns import SystemResolver
    from rastro.adapters.http import CurlTransport
    from rastro.application.acquisition import WebAcquirer
    from rastro.application.browser_acquisition import BrowserAcquirer

    transport, resolver = CurlTransport(), SystemResolver()
    http = WebAcquirer(
        job.acquisition, transport=transport, resolver=resolver, conditional=conditional
    )
    clients: dict[str, AcquisitionPort] = {"http": http}
    for mode, renderer in renderers.items():
        client = BrowserAcquirer(
            job.acquisition.model_copy(update={"render": mode}),
            renderer=renderer,
            transport=transport,
            resolver=resolver,
        )
        client._hosts = client._http._hosts = http._hosts
        client._slots = http._slots
        clients[mode] = client
    return _JobAcquisition(modes, clients, job.acquisition.render)
