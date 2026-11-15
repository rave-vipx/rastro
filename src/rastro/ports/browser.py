"""Optional rendered-document capability, independent of browser libraries."""

from typing import Protocol, runtime_checkable

from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult, PinnedTarget


@runtime_checkable
class BrowserRenderer(Protocol):
    async def render(
        self,
        target: PinnedTarget,
        *,
        policy: AcquisitionPolicy,
        max_bytes: int,
        targets: tuple[PinnedTarget, ...],
    ) -> AcquisitionResult: ...
