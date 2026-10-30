"""Ports between source policy, DNS resolution, and a pinned HTTP transfer."""

from typing import Protocol

from rastro.domain.acquisition import AcquisitionResult, HttpResponse, PinnedTarget, ProxyRoute
from rastro.domain.models import Representation, Source


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class HttpTransport(Protocol):
    """``proxy`` is None for direct egress, else the explicitly selected profile's route."""

    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None,
    ) -> HttpResponse: ...


class AcquisitionPort(Protocol):
    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        """Return the decoded HTML or JSON document requested by `representation`."""
        ...
