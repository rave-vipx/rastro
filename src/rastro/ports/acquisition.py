"""Ports between source policy, DNS resolution, and a pinned HTTP transfer."""

from typing import Protocol, runtime_checkable

from rastro.domain.acquisition import AcquisitionResult, HttpResponse, PinnedTarget, ProxyRoute
from rastro.domain.models import Representation, Source
from rastro.domain.source_notes import BrowserFamily, HttpVersion


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class HttpTransport(Protocol):
    """``proxy`` is None for direct egress, else the explicitly selected profile's route.

    ``impersonate`` names a browser whose TLS and header fingerprint to present, and
    ``http_version`` the protocol to ask for; None keeps the plain profile (HTTP/1.1, or the
    impersonated browser's own protocol). The acquirer passes them only when a job sets
    them, so older transports keep working. Neither may weaken pinning or redirects.
    """

    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None,
        impersonate: BrowserFamily | None = None,
        http_version: HttpVersion | None = None,
    ) -> HttpResponse: ...


class AcquisitionPort(Protocol):
    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        """Return the decoded HTML or JSON document requested by `representation`."""
        ...


@runtime_checkable
class XmlAcquisition(Protocol):
    """Fetch an XML document (a sitemap) under the same policy; bytes in ``raw_body`` (N16)."""

    async def acquire_xml(self, source: Source, *, max_bytes: int) -> AcquisitionResult: ...
