"""Ports between source policy, DNS resolution, and a pinned HTTP transfer."""

from typing import Protocol

from rastro.domain.acquisition import AcquisitionResult, HttpResponse, PinnedTarget
from rastro.domain.models import Representation, Source


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class HttpTransport(Protocol):
    async def get(
        self, target: PinnedTarget, *, headers: dict[str, str], timeout: float, max_bytes: int
    ) -> HttpResponse: ...


class AcquisitionPort(Protocol):
    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        """Return the decoded document; the engine passes `representation` only for JSON."""
        ...
