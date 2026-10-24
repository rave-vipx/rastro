"""Resolve through the platform resolver without blocking the event loop."""

import asyncio
import socket

from rastro.domain.acquisition import AcquisitionError
from rastro.domain.models import Outcome


class SystemResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            entries = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except OSError:
            raise AcquisitionError("dns_failure", Outcome.TRANSIENT_FAILURE) from None
        return tuple(dict.fromkeys(str(entry[4][0]) for entry in entries))
