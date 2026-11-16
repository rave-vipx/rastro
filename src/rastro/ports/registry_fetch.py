"""Port: fetch one registry file as bytes (N10). Adapters enforce the network policy."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RegistryFetcher(Protocol):
    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        """Return the body of ``url`` (at most ``max_bytes``) or raise ``RegistryError``."""
        ...
