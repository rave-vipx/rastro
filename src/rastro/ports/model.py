"""Models return untrusted proposal text; they receive no acquisition or execution tools."""

from typing import Protocol

from rastro.domain.intelligence import InferencePolicy, ModelRequest


class ModelPort(Protocol):
    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        """Return bounded JSON text, or raise ModelError; respect task cancellation."""
        ...
