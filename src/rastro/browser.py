"""Composition for job-selected HTTP, plain browser, and consented stealth acquisition."""

from __future__ import annotations

from rastro.application.operator_trust import current_trust
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, Representation, Source
from rastro.ports.acquisition import AcquisitionPort


def job_render_modes(job: Job) -> dict[str, str]:
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
    if (
        "stealth" in modes.values() or job.acquisition.solve_challenges
    ) and not current_trust().allow_stealth:
        raise ConfigurationError(
            "Stealth requires --allow-stealth, allow_stealth=True or RASTRO_ALLOW_STEALTH=1"
        )
    return modes


class _JobAcquisition:
    def __init__(self, modes: dict[str, str], clients: dict[str, AcquisitionPort]):
        self.modes, self.clients = modes, clients

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        return await self.clients[self.modes[source.url]].acquire(
            source, max_bytes=max_bytes, representation=representation
        )


def job_acquisition(job: Job, supplied: AcquisitionPort | None) -> AcquisitionPort | None:
    modes = job_render_modes(job)
    if supplied is not None or not any(mode != "http" for mode in modes.values()):
        return supplied
    from rastro.adapters.playwright_browser import PlaywrightBrowser
    from rastro.adapters.scrapling_stealth import ScraplingStealth

    assert job.acquisition is not None
    renderers = {
        mode: (ScraplingStealth() if mode == "stealth" else PlaywrightBrowser())
        for mode in set(modes.values()) - {"http"}
    }
    from rastro.adapters.dns import SystemResolver
    from rastro.adapters.http import CurlTransport
    from rastro.application.acquisition import WebAcquirer
    from rastro.application.browser_acquisition import BrowserAcquirer

    transport, resolver = CurlTransport(), SystemResolver()
    http = WebAcquirer(job.acquisition, transport=transport, resolver=resolver)
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
    return _JobAcquisition(modes, clients)
